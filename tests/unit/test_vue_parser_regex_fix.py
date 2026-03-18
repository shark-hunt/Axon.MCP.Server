"""Test Vue parser regex fix for malformed HTML tags."""
import pytest
from src.parsers.vue_parser import VueParser


def test_normal_script_tag():
    """Test extraction of normal well-formed script tags."""
    parser = VueParser()
    vue_code = """
<template>
  <div>Hello</div>
</template>

<script lang="ts">
export default {
  name: 'TestComponent'
}
</script>

<style scoped>
div { color: red; }
</style>
"""
    content, lang, offset = parser._extract_script_section(vue_code)
    assert content == "export default {\n  name: 'TestComponent'\n}"
    assert lang == 'ts'
    assert offset > 0


def test_malformed_script_tag_with_space():
    """Test extraction handles malformed closing tags like </script >."""
    parser = VueParser()
    vue_code = """
<template>
  <div>Hello</div>
</template>

<script lang="ts">
export default {
  name: 'TestComponent'
}
</script >

<style scoped>
div { color: red; }
</style>
"""
    content, lang, offset = parser._extract_script_section(vue_code)
    assert content == "export default {\n  name: 'TestComponent'\n}"
    assert lang == 'ts'


def test_malformed_script_tag_multiple_spaces():
    """Test extraction handles closing tags with multiple spaces."""
    parser = VueParser()
    vue_code = """
<script>
console.log('test');
</script   >
"""
    content, lang, offset = parser._extract_script_section(vue_code)
    assert "console.log('test');" in content
    assert lang == 'js'


def test_malformed_template_tag():
    """Test template extraction handles malformed closing tags."""
    parser = VueParser()
    vue_code = """
<template>
  <div>Hello World</div>
</template >

<script>
export default {}
</script>
"""
    template_content = parser._extract_template_section(vue_code)
    assert template_content == '<div>Hello World</div>'


def test_normal_template_tag():
    """Test template extraction with normal closing tag still works."""
    parser = VueParser()
    vue_code = """
<template>
  <div>Hello World</div>
</template>

<script>
export default {}
</script>
"""
    template_content = parser._extract_template_section(vue_code)
    assert template_content == '<div>Hello World</div>'


def test_malformed_script_tag_with_attributes():
    """Test extraction handles closing tags with attributes (browser validity quirk)."""
    parser = VueParser()
    vue_code = """
<script>
const x = 1;
</script foo="bar">
"""
    content, lang, offset = parser._extract_script_section(vue_code)
    assert "const x = 1;" in content
    assert lang == 'js'


def test_malformed_template_tag_with_newline_and_attrs():
    """Test template extraction handles closing tags with newlines and attributes."""
    parser = VueParser()
    vue_code = """
<template>
  <div>Content</div>
</template
  id="oops" >

<script>
export default {}
</script>
"""
    template_content = parser._extract_template_section(vue_code)
    assert template_content == '<div>Content</div>'


if __name__ == "__main__":
    # Run manual tests
    test_normal_script_tag()
    test_malformed_script_tag_with_space()
    test_malformed_script_tag_multiple_spaces()
    test_malformed_script_tag_with_attributes()  # New test
    test_malformed_template_tag()
    test_malformed_template_tag_with_newline_and_attrs()  # New test
    test_normal_template_tag()
    print("✓ All robust manual tests passed!")
