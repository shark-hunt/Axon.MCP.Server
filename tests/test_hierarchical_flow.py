import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock, mock_open
from src.database.models import Repository, Project, Symbol, Service
from src.analyzers.service_boundary_analyzer import ServiceBoundaryAnalyzer
from src.generators.service_doc_generator import ServiceDocGenerator
from src.mcp_server.tools.service_tools import list_services, get_service_details, get_service_documentation
from src.config.enums import SymbolKindEnum

@pytest.mark.asyncio
async def test_hierarchical_flow():
    """
    Test the complete flow:
    1. Service Detection (Analyzer)
    2. Documentation Generation (Generator)
    3. Tool Access (MCP Tools)
    """
    print("\nStarting End-to-End Integration Test...")
    
    # --- Setup Mocks ---
    mock_session = AsyncMock()
    mock_repo = Repository(
        id=1,
        name="Axon.IntegrationTest",
        path_with_namespace="test/axon-integration",
        url="https://example.com/test/axon-integration",
        clone_url="https://example.com/test/axon-integration.git"
    )
    
    # Mock Project (The "Service")
    project = Project(
        id=101,
        repository_id=1,
        name="Axon.Orders.API",
        file_path="/src/Axon.Orders.API/Axon.Orders.API.csproj",
        root_namespace="Axon.Orders.API",
        target_framework="net8.0"
    )
    
    # Mock Controller (The Signal)
    controller = Symbol(
        id=200,
        name="OrdersController",
        kind=SymbolKindEnum.CLASS,
        fully_qualified_name="Axon.Orders.API.Controllers.OrdersController",
        service_id=None  # service_id set later
    )
    
    # --- Step 1: Service Detection ---
    print("Step 1: Running Service Detection...")
    analyzer = ServiceBoundaryAnalyzer()
    
    # Mock DB queries for Analyzer
    # The analyzer uses async SQLAlchemy 2.0 style: session.execute(select(...))
    # We need to mock session.execute to return appropriate results
    
    async def execute_side_effect(query):
        """Mock session.execute to return different results based on query type"""
        mock_result = MagicMock()
        query_str = str(query).lower()
        
        # Check table names to be more specific
        # Check if it's a Project query (first query in detect_services)
        if "from projects" in query_str or "projects.id" in query_str:
            mock_result.scalars.return_value.all.return_value = [project]
        # Check if it's an existing service query (scalar_one_or_none)
        elif "from services" in query_str or "services.id" in query_str:
            # scalar_one_or_none is a method, so we need to mock it as a callable
            mock_result.scalar_one_or_none = MagicMock(return_value=None)
        # Check if it's a Symbol query (for controllers or updates)
        elif "from symbols" in query_str or "symbols.id" in query_str or "update symbols" in query_str:
            mock_result.scalars.return_value.all.return_value = [controller]
        else:
            mock_result.scalars.return_value.all.return_value = []
            mock_result.scalar_one_or_none = MagicMock(return_value=None)
            
        return mock_result
    
    mock_session.execute.side_effect = execute_side_effect
    
    # Mock session.add and session.flush for service creation
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    
    # Mock file ops for project file reading using pathlib.Path
    # The analyzer uses Path(project.file_path).exists() and Path(project.file_path).read_text()
    from pathlib import Path
    
    with patch.object(Path, 'exists', return_value=True):
        with patch.object(Path, 'read_text', return_value='<Project Sdk="Microsoft.NET.Sdk.Web">'):
            # FIXED: Added await since detect_services is an async method
            detected_services = await analyzer.detect_services(mock_repo, mock_session)
            
    assert len(detected_services) == 1
    service = detected_services[0]
    service.id = 500 # Simulate DB ID assignment
    print(f"  -> Detected Service: {service.name} ({service.service_type})")
    
    # Link Controller to Service (Simulate DB persistence)
    controller.service_id = service.id
    
    # --- Step 2: Documentation Generation ---
    print("Step 2: Generating Documentation...")
    
    # Mock DB queries for Generator
    # The generator uses session.execute() for multiple queries
    # We need to reset the side_effect and set up new mocks
    
    async def generator_execute_side_effect(query):
        """Mock session.execute for generator queries"""
        mock_result = MagicMock()
        query_str = str(query)
        
        # Controller query
        if "FROM symbols" in query_str.lower() and "controller" in query_str.lower():
            mock_result.scalars.return_value.all.return_value = [controller]
        # Method query
        elif "FROM symbols" in query_str.lower() and "method" in query_str.lower():
            mock_result.scalars.return_value.all.return_value = []
        # Symbol IDs query
        elif "symbols.id" in query_str.lower():
            mock_result.all.return_value = [(controller.id,)]
        # Other queries (dependencies, references, events, etc.)
        else:
            mock_result.scalars.return_value.all.return_value = []
            
        return mock_result
    
    mock_session.execute.side_effect = generator_execute_side_effect
    
    generator = ServiceDocGenerator(mock_session)
    
    # Mock LLM
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Manages customer orders."
    mock_client.chat.completions.create.return_value = mock_response
    
    with patch.object(generator.llm, '_get_client', return_value=mock_client):
        doc = await generator.generate_service_doc(service)
        
    assert "# Axon.Orders.API" in doc
    assert "Manages customer orders" in doc
    print("  -> Documentation generated successfully.")
    
    # --- Step 3: MCP Tool Access ---
    print("Step 3: Verifying MCP Tools...")
    
    # Mock DB session context manager for tools
    # get_async_session() is an async context manager, so we need to mock it properly
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def mock_get_async_session():
        yield mock_session
    
    # Mock DB results for tools
    # list_services
    # Reset the side_effect from Step 2 and set a simple return_value
    mock_session.execute.side_effect = None
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value.all.return_value = [service]
    mock_session.execute.return_value = mock_execute_result
    
    with patch("src.mcp_server.tools.service_tools.get_async_session", side_effect=mock_get_async_session):
        # Test list_services
        tools_result = await list_services()
        assert "Axon.Orders.API" in tools_result[0].text
        print("  -> list_services: OK")
        
        # Test get_service_details
        # Need to handle the specific query flow in get_service_details
        # session.execute is async, so it should return a coroutine that resolves to the result.
        # AsyncMock handles the coroutine part, but the return_value should be the result object.
        
        async def execute_side_effect(query):
            mock_result = MagicMock()
            if "FROM services" in str(query):
                 mock_result.scalars.return_value.all.return_value = [service]
                 mock_result.scalar_one_or_none.return_value = service
            elif "FROM symbols" in str(query):
                 mock_result.scalars.return_value.all.return_value = [controller]
            return mock_result
        mock_session.execute.side_effect = execute_side_effect
        
        details_result = await get_service_details("Axon.Orders.API")
        assert "OrdersController" in details_result[0].text
        print("  -> get_service_details: OK")
        
        # Test get_service_documentation
        # We need to mock ServiceDocGenerator inside the tool or mock the generator result
        with patch("src.mcp_server.tools.service_tools.ServiceDocGenerator") as MockGen:
            mock_gen_instance = MockGen.return_value
            mock_gen_instance.generate_service_doc = AsyncMock(return_value=doc)
            
            doc_result = await get_service_documentation("Axon.Orders.API")
            assert "Manages customer orders" in doc_result[0].text
            print("  -> get_service_documentation: OK")

    print("\nIntegration Test Completed Successfully! ✅")
