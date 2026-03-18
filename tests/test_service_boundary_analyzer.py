import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.analyzers.service_boundary_analyzer import ServiceBoundaryAnalyzer
from src.database.models import Repository, Project, Symbol, Service
from src.config.enums import SymbolKindEnum

# Helper for robust filesystem mocking
class MockFileSystem:
    def __init__(self, files=None):
        self.files = files or {} # path -> content

    def exists(self, path):
        # Allow checking parent directories implicitly if needed, 
        # but for this analyzer it checks specific files
        path_str = str(path).replace("\\", "/")
        return path_str in self.files

    def read_text(self, path, encoding='utf-8'):
        path_str = str(path).replace("\\", "/")
        if path_str in self.files:
            return self.files[path_str]
        raise FileNotFoundError(f"Mock file not found: {path}")

@pytest.fixture
def analyzer():
    return ServiceBoundaryAnalyzer()

@pytest.fixture
def mock_session():
    return AsyncMock()

@pytest.fixture
def mock_repo():
    return Repository(id=1, name="Axon.Backend.Core")

@pytest.fixture
def mock_fs():
    return MockFileSystem()

@pytest.fixture
def apply_fs_mock(mock_fs):
    """Patches pathlib.Path methods to use the mock_fs."""
    # We patch pathlib.Path.exists and pathlib.Path.read_text
    # Since the analyzer uses asyncio.to_thread, these methods will be called in a thread
    # robustly mocking them works fine with Standard Library mocks
    
    def mock_exists(self):
        return mock_fs.exists(str(self))
        
    def mock_read_text(self, encoding='utf-8', errors=None):
        return mock_fs.read_text(str(self))

    with patch("pathlib.Path.exists", side_effect=mock_exists, autospec=True) as exists_mock, \
         patch("pathlib.Path.read_text", side_effect=mock_read_text, autospec=True) as read_mock:
        yield

@pytest.fixture
def mock_settings():
    with patch("src.config.settings.get_settings") as mock_get:
        settings_mock = MagicMock()
        settings_mock.detect_library_services = True
        settings_mock.min_library_symbols = 10
        mock_get.return_value = settings_mock
        yield settings_mock

# Helper to setup database query results
def setup_db_mock(mock_session, projects=None, symbols=None, services=None, controller_symbols=None):
    projects = projects or []
    symbols = symbols or []
    controller_symbols = controller_symbols or []
    
    async def execute_side_effect(statement):
        mock_result = MagicMock()
        stmt_str = str(statement)
        
        if "FROM projects" in stmt_str or "projects" in stmt_str.lower():
            mock_scalars = MagicMock()
            mock_result.scalars.return_value = mock_scalars
            mock_scalars.all.return_value = projects
            
        elif "FROM symbols" in stmt_str or "symbols" in stmt_str.lower():
            # Differentiate by checking if it's a COUNT query
            # Must be careful not to match columns like "token_count"
            if "count(" in stmt_str.lower():
                # Library detection check
                mock_result.scalar.return_value = len(symbols)
            else:
                # Controller search query (selects actual symbol objects)
                # usage: result.scalars().all()
                # We must configure the intermediate mock for scalars()
                mock_scalars = MagicMock()
                mock_result.scalars.return_value = mock_scalars
                mock_scalars.all.return_value = controller_symbols
                
        elif "FROM services" in stmt_str or "services" in stmt_str.lower():
            mock_result.scalar_one_or_none.return_value = None
        else:
            mock_result.scalars.return_value.all.return_value = []
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalar.return_value = 0
            
        return mock_result

    mock_session.execute.side_effect = execute_side_effect

@pytest.mark.asyncio
async def test_detect_web_sdk_project(analyzer, mock_session, mock_repo, mock_settings, mock_fs, apply_fs_mock):
    # Setup Data
    project = Project(
        id=101,
        repository_id=1,
        name="Axon.Appointment.API",
        file_path="/src/Axon.Appointment.API/Axon.Appointment.API.csproj",
        root_namespace="Axon.Appointment.API",
        target_framework="net8.0"
    )
    
    # Setup Filesystem
    mock_fs.files = {
        "/src/Axon.Appointment.API/Axon.Appointment.API.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web">'
    }
    
    # Setup Database
    setup_db_mock(mock_session, projects=[project], symbols=[])
    
    # Run
    services = await analyzer.detect_services(mock_repo, mock_session)
            
    # Verify
    assert len(services) == 1
    assert services[0].name == "Axon.Appointment.API"
    assert services[0].service_type == "API"
    assert "Web SDK detected" in services[0].description

@pytest.mark.asyncio
async def test_detect_controller_project(analyzer, mock_session, mock_repo, mock_settings, mock_fs, apply_fs_mock):
    # Setup Data
    project = Project(
        id=102,
        repository_id=1,
        name="Axon.Users.API",
        file_path="/src/Axon.Users.API/Axon.Users.API.csproj",
        root_namespace="Axon.Users.API"
    )
    
    mock_controller = Symbol(
        name="UsersController", 
        kind=SymbolKindEnum.CLASS, 
        fully_qualified_name="Axon.Users.API.Controllers.UsersController",
        project_id=102
    )
    
    # Setup Filesystem (Standard SDK but has controllers)
    mock_fs.files = {
        "/src/Axon.Users.API/Axon.Users.API.csproj": '<Project Sdk="Microsoft.NET.Sdk">'
    }
    
    # Setup Database
    setup_db_mock(mock_session, projects=[project], controller_symbols=[mock_controller])
    
    # Run
    services = await analyzer.detect_services(mock_repo, mock_session)
            
    # Verify
    assert len(services) == 1
    assert services[0].name == "Axon.Users.API"
    assert "Found 1 Controllers" in services[0].description

@pytest.mark.asyncio
async def test_ignore_class_library(analyzer, mock_session, mock_repo, mock_settings, mock_fs, apply_fs_mock):
    # Setup Data
    project = Project(
        id=103,
        repository_id=1,
        name="Axon.Domain",
        file_path="/src/Axon.Domain/Axon.Domain.csproj",
        root_namespace="Axon.Domain",
        output_type="Library"
    )
    
    # Setup Filesystem
    mock_fs.files = {
        "/src/Axon.Domain/Axon.Domain.csproj": '<Project Sdk="Microsoft.NET.Sdk">'
    }
    
    # Setup Database (Few symbols, below threshold)
    # We create 5 symbols
    symbols = [Symbol(id=i, project_id=103) for i in range(5)]
    setup_db_mock(mock_session, projects=[project], symbols=symbols)
    
    # Run
    services = await analyzer.detect_services(mock_repo, mock_session)
            
    # Verify
    assert len(services) == 0

@pytest.mark.asyncio
async def test_detect_cqrs_host_project(analyzer, mock_session, mock_repo, mock_settings, mock_fs, apply_fs_mock):
    # Setup Data
    project = Project(
        id=104,
        repository_id=1,
        name="Axon.Worker.Host",
        file_path="/src/Axon.Worker.Host/Axon.Worker.Host.csproj",
        root_namespace="Axon.Worker.Host"
    )
    
    # Program.cs content
    program_cs_content = """
    var builder = Host.CreateApplicationBuilder(args);
    builder.Services.AddMediatR(cfg => cfg.RegisterServicesFromAssembly(typeof(Program).Assembly));
    builder.Services.AddMassTransit(x => x.UsingRabbitMq());
    var host = builder.Build();
    host.Run();
    """
    
    # Setup Filesystem
    mock_fs.files = {
        "/src/Axon.Worker.Host/Axon.Worker.Host.csproj": '<Project Sdk="Microsoft.NET.Sdk.Worker">',
        "/src/Axon.Worker.Host/Program.cs": program_cs_content
    }
    
    # Setup Database
    setup_db_mock(mock_session, projects=[project], symbols=[])
    
    # Run
    services = await analyzer.detect_services(mock_repo, mock_session)
            
    # Verify
    assert len(services) == 1
    assert services[0].name == "Axon.Worker.Host"
    assert "Host Builder detected" in services[0].description
    assert "CQRS (MediatR) detected" in services[0].description
