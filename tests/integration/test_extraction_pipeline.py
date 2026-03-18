import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from src.extractors.knowledge_extractor import KnowledgeExtractor
from src.database.models import File, Symbol, Chunk, Relation, Repository
from src.config.enums import LanguageEnum, SymbolKindEnum, RelationTypeEnum
from src.parsers.base_parser import ParseResult, ParsedSymbol

@pytest.fixture
def mock_session():
    """Mock database session."""
    session = MagicMock(spec=AsyncMock)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock()
    return session

@pytest.fixture
def mock_resolver():
    """Mock project resolver."""
    resolver = MagicMock()
    resolver.get_project_for_file = AsyncMock(return_value=123)
    resolver.get_project_metadata = AsyncMock(return_value={"assembly_name": "TestAssembly"})
    return resolver

@pytest.fixture
def mock_chunker():
    """Mock symbol chunker."""
    chunker = MagicMock()
    # Return valid chunk dicts
    chunker.create_chunks_for_symbol.return_value = [
        {
            "content": "chunk content",
            "content_type": "code",
            "token_count": 10,
            "start_line": 1,
            "end_line": 10
        }
    ]
    return chunker

@pytest.mark.asyncio
async def test_end_to_end_extraction(mock_session, mock_resolver, mock_chunker):
    """Test complete extraction pipeline with mocks."""
    
    # 1. Setup Parse Result (mimic the structure causing issues)
    class_symbol = ParsedSymbol(
        kind=SymbolKindEnum.CLASS,
        name="TestClass",
        start_line=1,
        end_line=10,
        start_column=0,
        end_column=0,
        signature="public class TestClass",
        documentation="Test class documentation",
        fully_qualified_name="TestNamespace.TestClass"
    )
    
    method_symbol = ParsedSymbol(
        kind=SymbolKindEnum.METHOD,
        name="TestMethod",
        start_line=3,
        end_line=8,
        start_column=4,
        end_column=4,
        signature="public void TestMethod()",
        documentation="Test method documentation",
        fully_qualified_name="TestNamespace.TestClass.TestMethod",
        parent_name="TestNamespace.TestClass"
    )
    
    parse_result = ParseResult(
        language=LanguageEnum.CSHARP,
        file_path="test.cs",
        symbols=[class_symbol, method_symbol],
        imports=[],
        exports=[],
        parse_errors=[],
        parse_duration_ms=100.0
    )
    
    # 2. Setup Session Side Effects
    # Need to return a File object when queried
    mock_file = File(id=1, path="test.cs", repository_id=10)
    mock_file_result = MagicMock()
    mock_file_result.scalar_one_or_none.return_value = mock_file
    mock_file_result.one_or_none.return_value = (10, "test.cs")
    
    # Need to handle delete queries (just return something)
    mock_delete_result = MagicMock()
    
    # Need to handle enrichment query (empty for now)
    mock_enrichment_result = MagicMock()
    mock_enrichment_result.all.return_value = []
    
    # Configure execute side effect
    async def execute_side_effect(stmt):
        stmt_str = str(stmt)
        print(f"QUERY: {stmt_str}")
        if "FROM files" in stmt_str or "files" in stmt_str: # Looser check
             # Return file result
             return mock_file_result
        return mock_delete_result

    # Note: mocking specific query types accurately is hard with MagicMock inputs.
    # We'll rely on return_value chaining for generic calls and simple side effects.
    # Given execute is AsyncMock, we can set return_value to a generic mock that handles basic calls.
    
    generic_result = MagicMock()
    generic_result.scalar_one_or_none.return_value = mock_file # Default to file for simplicity
    generic_result.all.return_value = []
    
    # Use side_effect instead of return_value
    mock_session.execute.side_effect = execute_side_effect

    # 3. Patch dependencies
    with patch("src.extractors.knowledge_extractor.ProjectResolver", return_value=mock_resolver), \
         patch("src.extractors.knowledge_extractor.SymbolChunker", return_value=mock_chunker), \
         patch("src.extractors.knowledge_extractor.ServiceBoundaryAnalyzer"), \
         patch("src.extractors.knowledge_extractor.ChunkContextBuilder"):
         
        extractor = KnowledgeExtractor(mock_session)
        
        # ACT
        result = await extractor.extract_and_persist(parse_result, file_id=1)
        
        # ASSERT
        assert len(result.errors) == 0
        assert result.symbols_created == 2
        assert result.relations_created == 1
        
        # Verify session calls
        # 2 symbols + 1 relation + 2 chunks (1 per symbol per mock) = 5 adds
        # Wait, chunks? mock_chunker returns 1 chunk per symbol.
        # Symbols created: 2.
        # Chunks created: 2.
        # Relations: 1.
        # Total adds: 2 + 2 + 1 = 5. (Plus maybe dependencies/refs if any, none in mock).
        
        # Verify specific adds (Symbols)
        # We can check the arguments to session.add
        added_symbols = [args[0] for args, _ in mock_session.add.call_args_list if isinstance(args[0], Symbol)]
        assert len(added_symbols) == 2
        names = [s.name for s in added_symbols]
        assert "TestClass" in names
        assert "TestMethod" in names
        
        # Verify Relation
        added_relations = [args[0] for args, _ in mock_session.add.call_args_list if isinstance(args[0], Relation)]
        assert len(added_relations) == 1
        rel = added_relations[0]
        assert rel.relation_type == RelationTypeEnum.CONTAINS
        
        # Verify flushing - Critical for FKs
        assert mock_session.flush.call_count >= 2 # Once per symbol loop, etc.
        # Ideally check exact sequence, but call_count is a good start.

@pytest.mark.asyncio
async def test_re_extraction_replaces_old_symbols(mock_session, mock_resolver, mock_chunker):
    """Test that existing symbols are deleted."""
    
    parse_result = ParseResult(
        language=LanguageEnum.CSHARP,
        file_path="test.cs",
        symbols=[],
        imports=[],
        exports=[],
        parse_errors=[],
        parse_duration_ms=10.0
    )
    
    generic_result = MagicMock()
    generic_result.scalar_one_or_none.return_value = File(id=1, repository_id=10) # Mock file
    generic_result.one_or_none.return_value = (10, "test.cs")
    generic_result.all.return_value = []
    mock_session.execute.return_value = generic_result
    
    with patch("src.extractors.knowledge_extractor.ProjectResolver", return_value=mock_resolver), \
         patch("src.extractors.knowledge_extractor.SymbolChunker", return_value=mock_chunker), \
         patch("src.extractors.knowledge_extractor.ServiceBoundaryAnalyzer"), \
         patch("src.extractors.knowledge_extractor.ChunkContextBuilder"):
         
        extractor = KnowledgeExtractor(mock_session)
        
        await extractor.extract_and_persist(parse_result, file_id=1)
        
        # Verify delete called
        # We can check specific execute calls if we inspect arguments
        # But simpler: ensure execute was called for delete
        # delete(Symbol).where(...)
        # delete(Dependency).where(...)
        assert mock_session.execute.call_count >= 2

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
