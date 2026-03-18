import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from src.extractors.knowledge_extractor import KnowledgeExtractor, ExtractionResult
from src.parsers.base_parser import ParseResult, ParsedSymbol
from src.config.enums import LanguageEnum, SymbolKindEnum, AccessModifierEnum
from src.database.models import Symbol

@pytest.fixture
def mock_session():
    """Mock database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.add = Mock()
    return session

@pytest.fixture
def sample_parse_result():
    """Create sample parse result."""
    symbol = ParsedSymbol(
        kind=SymbolKindEnum.CLASS,
        name="TestClass",
        start_line=1,
        end_line=10,
        start_column=0,
        end_column=0,
        signature="public class TestClass",
        fully_qualified_name="Namespace.TestClass"
    )
    
    return ParseResult(
        language=LanguageEnum.CSHARP,
        file_path="test.cs",
        symbols=[symbol],
        imports=[],
        exports=[],
        parse_errors=[],
        parse_duration_ms=100.0
    )

@pytest.fixture
def sample_parse_result_with_method():
    """Create sample parse result with class and method."""
    class_symbol = ParsedSymbol(
        kind=SymbolKindEnum.CLASS,
        name="TestClass",
        start_line=1,
        end_line=10,
        start_column=0,
        end_column=0,
        signature="public class TestClass",
        fully_qualified_name="Namespace.TestClass"
    )
    
    method_symbol = ParsedSymbol(
        kind=SymbolKindEnum.METHOD,
        name="TestMethod",
        start_line=3,
        end_line=8,
        start_column=4,
        end_column=4,
        signature="public void TestMethod(string arg1, int arg2)",
        documentation="This is a test method",
        fully_qualified_name="Namespace.TestClass.TestMethod",
        parent_name="Namespace.TestClass",
        access_modifier=AccessModifierEnum.PUBLIC,
        parameters=[
            {"name": "arg1", "type": "string"},
            {"name": "arg2", "type": "int"}
        ],
        return_type="void"
    )
    
    return ParseResult(
        language=LanguageEnum.CSHARP,
        file_path="test.cs",
        symbols=[class_symbol, method_symbol],
        imports=[],
        exports=[],
        parse_errors=[],
        parse_duration_ms=100.0
    )

@pytest.mark.asyncio
async def test_extract_and_persist_basic(mock_session, sample_parse_result):
    """Test basic knowledge extraction."""
    with patch("src.extractors.knowledge_extractor.ProjectResolver") as MockResolver, \
         patch("src.extractors.knowledge_extractor.SymbolChunker") as MockChunker, \
         patch("src.extractors.knowledge_extractor.ServiceBoundaryAnalyzer") as MockAnalyzer:
        
        # Setup mocks
        mock_resolver = MockResolver.return_value
        mock_resolver.get_project_for_file = AsyncMock(return_value=1)
        mock_resolver.get_project_metadata = AsyncMock(return_value={"assembly_name": "TestAssembly"})
        
        mock_chunker = MockChunker.return_value
        mock_chunker.create_chunks_for_symbol = AsyncMock(return_value=[MagicMock()])
        
        # Configure session.execute to return a File object
        mock_file_result = MagicMock()
        mock_file_obj = MagicMock()
        mock_file_obj.repository_id = 1
        mock_file_obj.path = "test.cs"
        mock_file_result.scalar_one_or_none.return_value = mock_file_obj
        mock_session.execute.return_value = mock_file_result
        
        extractor = KnowledgeExtractor(mock_session)
        
        # Mock internal methods to isolate extract_and_persist logic
        extractor._create_solutions_and_projects = AsyncMock(return_value=(0, 0))
        extractor._create_dependencies = AsyncMock(return_value=0)
        extractor._create_project_references = AsyncMock(return_value=0)
        extractor._merge_partial_classes = AsyncMock()
        
        result = await extractor.extract_and_persist(
            sample_parse_result,
            file_id=1,
            commit_id=1
        )
        
        assert result.symbols_created == 1
        assert result.chunks_created >= 0
        assert len(result.errors) == 0
        mock_session.flush.assert_called()

@pytest.mark.asyncio
async def test_extract_and_persist_with_relationships(mock_session, sample_parse_result_with_method):
    """Test extraction with parent-child relationships."""
    with patch("src.extractors.knowledge_extractor.ProjectResolver") as MockResolver, \
         patch("src.extractors.knowledge_extractor.SymbolChunker") as MockChunker, \
         patch("src.extractors.knowledge_extractor.ServiceBoundaryAnalyzer") as MockAnalyzer:
        
        # Setup mocks
        mock_resolver = MockResolver.return_value
        mock_resolver.get_project_for_file = AsyncMock(return_value=1)
        mock_resolver.get_project_metadata = AsyncMock(return_value={"assembly_name": "TestAssembly"})
        
        mock_chunker = MockChunker.return_value
        mock_chunker.create_chunks_for_symbol = AsyncMock(return_value=[MagicMock()])
        
        # Configure session.execute to return a File object
        mock_file_result = MagicMock()
        mock_file_obj = MagicMock()
        mock_file_obj.repository_id = 1
        mock_file_obj.path = "test.cs"
        mock_file_result.scalar_one_or_none.return_value = mock_file_obj
        mock_session.execute.return_value = mock_file_result
        
        extractor = KnowledgeExtractor(mock_session)
        
        # Mock internal methods
        extractor._create_solutions_and_projects = AsyncMock(return_value=(0, 0))
        extractor._create_dependencies = AsyncMock(return_value=0)
        extractor._create_project_references = AsyncMock(return_value=0)
        extractor._merge_partial_classes = AsyncMock()
        
        result = await extractor.extract_and_persist(
            sample_parse_result_with_method,
            file_id=1,
            commit_id=1
        )
        
        assert result.symbols_created == 2
        assert result.relations_created == 1  # Parent-child relationship
        assert result.chunks_created >= 0
        assert len(result.errors) == 0
        mock_session.flush.assert_called()

def test_calculate_complexity_function():
    """Test complexity calculation for functions."""
    mock_session = AsyncMock()
    extractor = KnowledgeExtractor(mock_session)
    
    # Function with parameters
    parsed = ParsedSymbol(
        kind=SymbolKindEnum.FUNCTION,
        name="testFunc",
        start_line=1,
        end_line=5,
        start_column=0,
        end_column=0,
        signature="function testFunc(a, b, c)",
        parameters=[
            {"name": "a", "type": "string"},
            {"name": "b", "type": "int"},
            {"name": "c", "type": "bool"}
        ]
    )
    
    complexity = extractor._calculate_complexity(parsed)
    
    assert complexity == 4  # Base complexity 1 + 3 parameters

def test_calculate_complexity_class():
    """Test complexity calculation for classes."""
    mock_session = AsyncMock()
    extractor = KnowledgeExtractor(mock_session)
    
    parsed = ParsedSymbol(
        kind=SymbolKindEnum.CLASS,
        name="TestClass",
        start_line=1,
        end_line=10,
        start_column=0,
        end_column=0,
        signature="public class TestClass"
    )
    
    complexity = extractor._calculate_complexity(parsed)
    
    assert complexity == 0  # Classes have no complexity by default

@pytest.mark.asyncio
async def test_extraction_with_errors(mock_session):
    """Test extraction handles errors gracefully."""
    with patch("src.extractors.knowledge_extractor.ProjectResolver") as MockResolver, \
         patch("src.extractors.knowledge_extractor.SymbolChunker") as MockChunker:
        
        # Setup mocks
        mock_resolver = MockResolver.return_value
        mock_resolver.get_project_for_file = AsyncMock(return_value=1)
        mock_resolver.get_project_metadata = AsyncMock(return_value={})
        
        mock_chunker = MockChunker.return_value
        mock_chunker.create_chunks_for_symbol = AsyncMock(return_value=[MagicMock()])
        
        # Configure session.execute to return a File object
        mock_file_result = MagicMock()
        mock_file_obj = MagicMock()
        mock_file_obj.repository_id = 1
        mock_file_obj.path = "test.cs"
        mock_file_result.scalar_one_or_none.return_value = mock_file_obj
        mock_session.execute.return_value = mock_file_result
        
        mock_session.flush.side_effect = Exception("Database error")
        
        extractor = KnowledgeExtractor(mock_session)
        
        # Mock internal methods
        extractor._create_solutions_and_projects = AsyncMock(return_value=(0, 0))
        # Dependencies and refs run BEFORE symbol creation/flush?
        # extract_and_persist order:
        # 1. delete...
        # 2. _create_symbol ... flush
        # 3. _create_chunks
        # 4. _create_dependencies
        
        extractor._create_dependencies = AsyncMock(return_value=0)
        extractor._create_project_references = AsyncMock(return_value=0)
        extractor._merge_partial_classes = AsyncMock()
        
        symbol = ParsedSymbol(
            kind=SymbolKindEnum.CLASS,
            name="TestClass",
            start_line=1,
            end_line=10,
            start_column=0,
            end_column=0,
            signature="public class TestClass",
            fully_qualified_name="Namespace.TestClass"
        )
        
        parse_result = ParseResult(
            language=LanguageEnum.CSHARP,
            file_path="test.cs",
            symbols=[symbol],
            imports=[],
            exports=[],
            parse_errors=[],
            parse_duration_ms=100.0
        )
        
        # The code now handles errors gracefully and captures them in result.errors
        # instead of re-raising them (per-symbol errors are caught, logged, and processing continues)
        result = await extractor.extract_and_persist(
            parse_result,
            file_id=1,
            commit_id=1
        )
        
        # Verify error was caught and captured in result.errors
        assert len(result.errors) > 0, "Expected errors to be captured"
        assert any("Database error" in err or "Failed" in err for err in result.errors), \
            f"Expected database error in result.errors, got: {result.errors}"
        
        # Verify rollback was called when the flush failed
        # (The code calls rollback in the per-symbol exception handler at line 186)
        mock_session.rollback.assert_called()



@pytest.mark.asyncio
async def test_create_symbol():
    """Test symbol creation from parsed symbol."""
    mock_session = AsyncMock()
    extractor = KnowledgeExtractor(mock_session)
    
    parsed = ParsedSymbol(
        kind=SymbolKindEnum.METHOD,
        name="TestMethod",
        start_line=5,
        end_line=10,
        start_column=4,
        end_column=4,
        signature="public async Task<string> TestMethod(int id, string name)",
        documentation="Test method that does something",
        fully_qualified_name="MyNamespace.MyClass.TestMethod",
        parent_name="MyNamespace.MyClass",
        access_modifier=AccessModifierEnum.PUBLIC,
        parameters=[
            {"name": "id", "type": "int"},
            {"name": "name", "type": "string"}
        ],
        return_type="Task<string>"
    )
    
    symbol = await extractor._create_symbol(
        parsed,
        file_id=1,
        commit_id=2,
        language=LanguageEnum.CSHARP
    )
    
    assert symbol.file_id == 1
    assert symbol.commit_id == 2
    assert symbol.language == LanguageEnum.CSHARP
    assert symbol.kind == SymbolKindEnum.METHOD
    assert symbol.access_modifier == AccessModifierEnum.PUBLIC
    assert symbol.name == "TestMethod"
    assert symbol.fully_qualified_name == "MyNamespace.MyClass.TestMethod"
    assert symbol.start_line == 5
    assert symbol.end_line == 10
    assert symbol.signature == parsed.signature
    assert symbol.documentation == parsed.documentation
    assert symbol.parameters == parsed.parameters
    assert symbol.return_type == "Task<string>"
    assert symbol.complexity > 0
    assert symbol.token_count > 0
