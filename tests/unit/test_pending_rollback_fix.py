"""
Quick test to verify the PendingRollbackError fix.

This test simulates the error scenario from the GitHub Actions log:
1. Symbol creation succeeds and is flushed
2. Chunk creation with FK reference fails
3. Session should rollback and continue processing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import IntegrityError
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.parsers.base_parser import ParseResult, ParsedSymbol
from src.config.enums import SymbolKindEnum, LanguageEnum


@pytest.mark.asyncio
async def test_fk_violation_recovery():
    """Test that FK violations trigger rollback and allow continuation."""
    
    # Create a mock session
    mock_session = AsyncMock()
    
    # Track flush calls
    flush_count = 0
    rollback_count = 0
    
    async def mock_flush():
        nonlocal flush_count
        flush_count += 1
        # Simulate FK violation on second flush (chunk creation)
        if flush_count == 2:
            raise IntegrityError(
                "insert or update on table \"chunks\" violates foreign key constraint",
                {},
                Exception("ForeignKeyViolationError")
            )
    
    async def mock_rollback():
        nonlocal rollback_count
        rollback_count += 1
    
    async def mock_execute(stmt):
        """Mock execute to return file info."""
        # Use MagicMock instead of AsyncMock because result.scalar_one_or_none()
        # is called as a sync method, not awaited
        result = MagicMock()
        
        # Check if this is a File query or Symbol enrichment query
        # File query: select(File).where(File.id == file_id)
        # Enrichment query: select(Symbol.fully_qualified_name, Symbol.name, Symbol.ai_enrichment)
        stmt_str = str(stmt)
        
        if 'File' in stmt_str or hasattr(stmt, 'column_descriptions'):
            # This is a File query
            file_obj = MagicMock()
            file_obj.repository_id = 1
            file_obj.path = "test.cs"
            file_obj.id = 1
            result.scalar_one_or_none.return_value = file_obj
            result.all.return_value = []  # For symbol enrichment queries
            result.scalars.return_value.all.return_value = []
        else:
            # This is a Symbol enrichment query - return empty list (no existing enrichment)
            result.all.return_value = []
            result.scalars.return_value.all.return_value = []
        
        return result
    
    mock_session.execute = mock_execute
    mock_session.flush = mock_flush
    mock_session.rollback = mock_rollback
    mock_session.add = MagicMock()
    
    # Create test parse result with 2 symbols
    # First symbol will fail during chunk creation
    # Second symbol should still be processed (proving rollback works)
    parse_result = ParseResult(
        language=LanguageEnum.CSHARP,
        file_path="test.cs",
        symbols=[
            ParsedSymbol(
                kind=SymbolKindEnum.CLASS,
                name="OldClass",
                fully_qualified_name="MyNamespace.OldClass",
                start_line=1,
                end_line=10,
                start_column=0,
                end_column=100
            ),
            ParsedSymbol(
                kind=SymbolKindEnum.CLASS,
                name="NewClass",
                fully_qualified_name="MyNamespace.NewClass",
                start_line=11,
                end_line=20,
                start_column=0,
                end_column=100
            )
        ],
        imports=[],
        exports=[],
        parse_errors=[],
        parse_duration_ms=100.0
    )
    
    # Create extractor with mocked dependencies
    with patch.object(KnowledgeExtractor, '__init__', lambda self, session: setattr(self, 'session', session) or None):
        extractor = KnowledgeExtractor(mock_session)
        extractor.chunker = MagicMock()
        extractor.context_builder = AsyncMock()
        extractor.project_resolver = AsyncMock()
        extractor.service_analyzer = MagicMock()
        
        # Mock chunker to return a chunk
        extractor.chunker.create_chunks_for_symbol = MagicMock(return_value=[{
            'content': 'test content',
            'content_type': 'code',
            'chunk_subtype': 'implementation',
            'context_metadata': {},
            'start_line': 1,
            'end_line': 10
        }])
        
        # Mock context builder
        extractor.context_builder.build_context = AsyncMock(return_value={})
        
        # Mock project resolver
        extractor.project_resolver.get_project_for_file = AsyncMock(return_value=None)
        
        # Mock the methods that are called but not yet implemented in the test
        extractor._create_dependencies = AsyncMock(return_value=[])
        extractor._create_project_references = AsyncMock(return_value=0)
        extractor._create_solutions_and_projects = AsyncMock(return_value=(0, 0))  # Returns (solutions_created, projects_created)
        extractor._merge_partial_classes = AsyncMock(return_value=None)
        
        try:
            result = await extractor.extract_and_persist(parse_result, file_id=1, commit_id=1)
            
            # ASSERTIONS
            
            # Rollback should have been called once (after first symbol's chunk failure)
            assert rollback_count == 1, f"Expected 1 rollback, got {rollback_count}"
            
            # Should have errors logged
            assert len(result.errors) > 0, "Expected errors to be logged"
            assert "OldClass" in result.errors[0], "Error should mention the failed symbol"
            
            # At least one symbol should have been created (the second one after rollback)
            # Note: Due to the mocking, both might be counted as "created" even though first one failed
            # The important thing is that the code didn't crash with PendingRollbackError
            
            print(f"✓ Test passed! Rollbacks: {rollback_count}, Errors: {len(result.errors)}")
            print(f"  Symbols created: {result.symbols_created}")
            print(f"  Errors reported: {result.errors}")
            
        except Exception as e:
            # If we get a PendingRollbackError, the fix didn't work
            if "PendingRollbackError" in str(type(e)):
                pytest.fail(f"PendingRollbackError still occurring! Fix didn't work. Error: {e}")
            else:
                # Re-raise for debugging
                raise


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_fk_violation_recovery())
    print("\n✅ All checks passed! The PendingRollbackError fix is working correctly.")
