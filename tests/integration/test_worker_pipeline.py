"""
Unit tests for worker pipeline orchestration.
Replaces integration tests to avoid real database dependencies.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config.enums import (
    JobStatusEnum,
    LanguageEnum,
    RepositoryStatusEnum,
    SymbolKindEnum,
    SourceControlProviderEnum
)
from src.database.models import File, Job, Repository
from src.parsers.base_parser import ParseResult, ParsedSymbol
from src.workers.tasks import _sync_repository_async


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock()
    session.expunge_all = MagicMock()
    
    # Support async context manager
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    return session


@pytest.fixture
def mock_celery_task():
    """Mock Celery task."""
    task = MagicMock()
    task.request.id = "test-task-id"
    task.request.retries = 0
    task.update_state = MagicMock()
    return task


@pytest.fixture
def sample_parse_result():
    """Sample parse result."""
    return ParseResult(
        language=LanguageEnum.PYTHON,
        file_path="test.py",
        symbols=[],
        imports=[],
        exports=[],
        parse_errors=[],
        parse_duration_ms=10.0
    )


class MockDBHelper:
    """Helper to handle common DB mock setup."""
    
    @staticmethod
    def create_execute_side_effect(repo, job):
        """Create a side effect for session.execute that handles varied query types."""
        def side_effect(*args, **kwargs):
            # Inspect the query object (usually args[0])
            query = args[0]
            str_query = str(query)
            mock_res = MagicMock()

            # Robust checking for query targets
            if "FROM repositories" in str_query:
                mock_res.scalar_one_or_none.return_value = repo
                mock_res.scalar_one.return_value = repo
                return mock_res
                
            elif "FROM jobs" in str_query:
                # If we are looking for a job, return our mocked job
                mock_res.scalar_one_or_none.return_value = job
                mock_res.scalar_one.return_value = job
                return mock_res
                
            elif "sum(files.size_bytes)" in str_query.lower() or "func.sum" in str_query:
                 mock_res.scalar.return_value = 1024
                 return mock_res
            
            # Default fallback for other queries (like Commit selection etc.)
            mock_res.scalar_one_or_none.return_value = None
            mock_res.scalar_one.return_value = None
            mock_res.scalar.return_value = None
            return mock_res
        return side_effect


@pytest.mark.asyncio
class TestWorkerPipeline:
    """Test the repository sync pipeline orchestration."""

    async def test_repository_sync_full_pipeline(
        self, mock_session, mock_celery_task, sample_parse_result
    ):
        """Test complete repository sync pipeline with mocks."""
        
        repo_id = 1
        
        # Setup mock data for Repo
        repo = MagicMock(spec=Repository)
        repo._sa_instance_state = MagicMock() 
        repo.id = repo_id
        repo.provider = SourceControlProviderEnum.GITLAB
        repo.url = "http://test"
        repo.path_with_namespace = "test/repo"
        repo.default_branch = "main"
        repo.status = RepositoryStatusEnum.PENDING
        
        # Setup mock data for Job
        job = MagicMock(spec=Job)
        job._sa_instance_state = MagicMock()
        job.id = 100
        job.status = JobStatusEnum.RUNNING
        job.job_metadata = {}
        job.retry_count = 0
        job.started_at = datetime.now()
        
        # Configure session behavior
        mock_session.execute.side_effect = MockDBHelper.create_execute_side_effect(repo, job)
        mock_session.get.side_effect = lambda model, id: repo if model == Repository else job

        # List of all steps used in the pipeline to ensure we mock them all
        # This prevents accidental real execution of steps (like DotnetRestoreStep)
        step_class_names = [
             "CloneStep",
             "DotnetRestoreStep",
             "RoslynInitStep",
             "DiscoveryStep",
             "ParsingStep",
             "ApiExtractionStep",
             "ReferenceBuildingStep",
             "RelationshipBuildingStep",
             "ImportResolutionStep",
             "CallGraphStep",
             "DependencyExtractionStep",
             "ConfigExtractionStep",
             "EfCoreExtractionStep",
             "PatternDetectionStep",
             "CombinedExtractionStep",
             "EmbeddingGenerationStep",
             "ServiceDetectionStep",
             "ServiceDocumentationStep"
        ]

        # Context manager nest to handle the many patches
        with patch("src.workers.sync_worker.AsyncSessionLocal", return_value=mock_session), \
             patch("src.workers.sync_worker.RedisLogPublisher") as MockPublisher, \
             patch("src.workers.sync_worker.get_distributed_lock") as MockLock, \
             patch("src.workers.sync_worker.ParserFactory.cleanup", new_callable=AsyncMock) as mock_cleanup, \
             patch("src.workers.sync_worker.celery_app.send_task") as mock_send_task, \
             patch("src.workers.sync_worker._generate_module_summaries", new_callable=AsyncMock) as mock_summaries:
            
            # Mock all steps dynamically
            step_patches = []
            mock_step_instances = {}
            
            # Start patches for steps
            for step_name in step_class_names:
                p = patch(f"src.workers.pipeline.steps.{lambda_step_module(step_name)}.{step_name}")
                mock_cls = p.start()
                step_patches.append(p)
                
                # Setup the instance returned by constructor
                mock_inst = mock_cls.return_value
                mock_inst.name = step_name
                mock_inst.run = AsyncMock() # The execute logic
                
                # Special setup for specific steps if needed
                if step_name == "DiscoveryStep":
                     # Simulate discovery populating files
                     def discovery_side_effect(ctx):
                         ctx.files = [Path("test.py")]
                     mock_inst.run.side_effect = discovery_side_effect
                     
                mock_step_instances[step_name] = mock_inst

            try:
                # Setup Publisher async methods
                pub_instance = MockPublisher.return_value
                pub_instance.connect = AsyncMock()
                pub_instance.clear_logs = AsyncMock()
                pub_instance.publish_log = AsyncMock()
                pub_instance.close = AsyncMock()

                MockLock.return_value.acquire.return_value.__enter__.return_value = True
                
                # Run the task
                result = await _sync_repository_async(mock_celery_task, repo_id)
                
                # Assertions
                assert result["status"] == "success"
                
                # Verify key steps were called
                assert mock_step_instances["CloneStep"].run.called
                assert mock_step_instances["ParsingStep"].run.called
                assert mock_step_instances["EmbeddingGenerationStep"].run.called
                
                # Verify commits occurred
                assert mock_session.commit.called
                
                # Verify repo status updated to COMPLETED
                assert repo.status == RepositoryStatusEnum.COMPLETED
                
                # Verify cleanup was called
                assert mock_cleanup.called
                
                # Verify enrichment was triggered
                mock_send_task.assert_called_with(
                    "src.workers.enrichment_worker.enrich_batch",
                    args=[repo_id],
                    countdown=10
                )
                
            finally:
                # Stop all step patches
                for p in step_patches:
                    p.stop()

    async def test_repository_sync_lock_taken(self, mock_session, mock_celery_task):
        """Test sync skips if lock is taken."""
        
        with patch("src.workers.sync_worker.get_distributed_lock") as MockLock, \
             patch("src.workers.sync_worker.RedisLogPublisher") as MockPublisher:
            
            MockLock.return_value.acquire.return_value.__enter__.return_value = False # Lock failed
            
            pub_instance = MockPublisher.return_value
            pub_instance.connect = AsyncMock()
            pub_instance.clear_logs = AsyncMock()
            pub_instance.close = AsyncMock()
            
            result = await _sync_repository_async(mock_celery_task, 1)
            
            assert result["status"] == "skipped"
            assert "already being processed" in result["reason"]

    async def test_repository_sync_error_handling(self, mock_session, mock_celery_task):
        """Test error handling rolls back and updates status."""
        
        repo = MagicMock(spec=Repository)
        repo._sa_instance_state = MagicMock()
        repo.id = 1
        
        job = MagicMock(spec=Job)
        job._sa_instance_state = MagicMock()
        job.id = 123
        job.retry_count = 0
        job.job_metadata = {}
        job.started_at = datetime.now()
        
        mock_session.execute.side_effect = MockDBHelper.create_execute_side_effect(repo, job)
        mock_session.get.side_effect = lambda model, id: repo if model == Repository else job 

        with patch("src.workers.sync_worker.AsyncSessionLocal", return_value=mock_session), \
             patch("src.workers.sync_worker.RedisLogPublisher") as MockPublisher, \
             patch("src.workers.sync_worker.get_distributed_lock") as MockLock, \
             patch("src.workers.sync_worker.ParserFactory.cleanup", new_callable=AsyncMock), \
             patch("src.workers.pipeline.steps.clone_step.CloneStep") as MockCloneStep_Cls:
             
            # Setup Publisher async methods
            pub_instance = MockPublisher.return_value
            pub_instance.connect = AsyncMock()
            pub_instance.clear_logs = AsyncMock()
            pub_instance.publish_log = AsyncMock()
            pub_instance.close = AsyncMock()

            MockLock.return_value.acquire.return_value.__enter__.return_value = True
            
            # Make clone step instance fail
            mock_clone_inst = MockCloneStep_Cls.return_value
            mock_clone_inst.run.side_effect = Exception("Clone failed")
            mock_clone_inst.name = "CloneStep"
            
            with pytest.raises(Exception, match="Clone failed"):
                await _sync_repository_async(mock_celery_task, 1)
            
            assert mock_session.rollback.called
            assert repo.status == RepositoryStatusEnum.FAILED


def lambda_step_module(class_name):
    """Helper to map class names to module names (snake_case conversion)."""
    # Simple mapping for known steps
    mapping = {
        "CloneStep": "clone_step",
        "DotnetRestoreStep": "dotnet_restore_step",
        "RoslynInitStep": "roslyn_init_step",
        "DiscoveryStep": "discovery_step",
        "ParsingStep": "parsing_step",
        "ApiExtractionStep": "api_extraction_step",
        "ReferenceBuildingStep": "reference_building_step",
        "RelationshipBuildingStep": "relationship_building_step",
        "ImportResolutionStep": "import_resolution_step",
        "CallGraphStep": "call_graph_step",
        "DependencyExtractionStep": "dependency_extraction_step",
        "ConfigExtractionStep": "config_extraction_step",
        "EfCoreExtractionStep": "ef_core_step",
        "PatternDetectionStep": "pattern_detection_step",
        "CombinedExtractionStep": "combined_extraction_step",
        "EmbeddingGenerationStep": "embedding_step",
        "ServiceDetectionStep": "service_detection_step",
        "ServiceDocumentationStep": "service_documentation_step"
    }
    return mapping.get(class_name, "unknown_step")

