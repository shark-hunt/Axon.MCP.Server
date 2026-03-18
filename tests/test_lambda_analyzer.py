import pytest
from tree_sitter import Language, Parser
import tree_sitter_c_sharp
from src.extractors.lambda_analyzer import LambdaAnalyzer
from src.parsers.base_parser import ParsedSymbol
from src.config.enums import SymbolKindEnum, AccessModifierEnum

class TestLambdaAnalyzer:
    
    @pytest.fixture
    def analyzer(self):
        return LambdaAnalyzer()
        
    @pytest.fixture
    def parser(self):
        return Parser(Language(tree_sitter_c_sharp.language()))

    def test_simple_lambda_extraction(self, analyzer, parser):
        code = """
        public void Process() {
            var items = new List<int> { 1, 2, 3 };
            var doubled = items.Select(x => x * 2).ToList();
        }
        """
        tree = parser.parse(bytes(code, "utf8"))
        root = tree.root_node
        
        # Find the method node
        method_node = None
        for child in root.children:
            if child.type == 'global_statement':
                for grandchild in child.children:
                    if grandchild.type in ['local_function_statement', 'method_declaration']:
                        method_node = grandchild
                        break
        
        assert method_node is not None, "Could not find method node in AST"
        
        lambdas = analyzer.extract_lambdas(method_node, code, "Process", "Test")
        
        assert len(lambdas) == 1
        lambda_obj = lambdas[0]
        assert lambda_obj.name == "Process.lambda_1"
        assert lambda_obj.linq_pattern == "Select"
        assert lambda_obj.parameters == ['x']

    def test_closure_capture(self, analyzer, parser):
        code = """
        public void Filter(int threshold) {
            int offset = 10;
            var items = new List<int>();
            var filtered = items.Where(x => x > threshold + offset);
        }
        """
        tree = parser.parse(bytes(code, "utf8"))
        root = tree.root_node
        
        # Find the method node
        method_node = None
        for child in root.children:
            if child.type == 'global_statement':
                for grandchild in child.children:
                    if grandchild.type in ['local_function_statement', 'method_declaration']:
                        method_node = grandchild
                        break
        
        assert method_node is not None, "Could not find method node in AST"
        
        lambdas = analyzer.extract_lambdas(method_node, code, "Filter", "Test")
        
        assert len(lambdas) == 1
        lambda_obj = lambdas[0]
        closure_vars = lambda_obj.closure_variables
        
        # 'threshold' is a parameter of parent, 'offset' is local in parent
        # Both should be captured by lambda
        assert 'threshold' in closure_vars
        assert 'offset' in closure_vars
        assert 'x' not in closure_vars # Lambda parameter

    def test_linq_method_chain(self, analyzer, parser):
        code = """
        public void Query() {
            var result = items.Where(x => x.IsActive)
                              .OrderBy(x => x.Name)
                              .Select(x => x.Id);
        }
        """
        tree = parser.parse(bytes(code, "utf8"))
        root = tree.root_node
        
        # Find the method node
        method_node = None
        for child in root.children:
            if child.type == 'global_statement':
                for grandchild in child.children:
                    if grandchild.type in ['local_function_statement', 'method_declaration']:
                        method_node = grandchild
                        break
        
        assert method_node is not None, "Could not find method node in AST"
        
        lambdas = analyzer.extract_lambdas(method_node, code, "Query", "Test")
        
        assert len(lambdas) == 3
        
        patterns = [l.linq_pattern for l in lambdas]
        assert "Where" in patterns
        assert "OrderBy" in patterns
        assert "Select" in patterns
