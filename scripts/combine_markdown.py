#!/usr/bin/env python3
"""
Combine markdown files with include directives.

Usage:
    python scripts/combine_markdown.py [--output OUTPUT]

Include syntax:
    <!-- include: path/to/file.md -->

The included content replaces the include directive.
Nested includes are supported.
"""

import argparse
import os
import re
from pathlib import Path


def find_includes(content: str) -> list[tuple[str, str, int]]:
    """Find all include directives in content. Returns list of (directive, filename, line_num)."""
    pattern = r'<!--\s*include:\s*([^\s]+)\s*-->'
    matches = []
    for i, line in enumerate(content.split('\n'), 1):
        match = re.search(pattern, line)
        if match:
            matches.append((match.group(0), match.group(1), i))
    return matches


def resolve_includes(content: str, base_dir: Path, max_depth: int = 5, _depth: int = 0) -> str:
    """Recursively resolve includes in markdown content."""
    if _depth > max_depth:
        raise ValueError(f"Maximum include depth ({max_depth}) exceeded")
    
    includes = find_includes(content)
    
    if not includes:
        return content
    
    # Process includes in reverse order to preserve line numbers
    for directive, filename, _ in reversed(includes):
        include_path = base_dir / filename
        
        if not include_path.exists():
            content = content.replace(
                directive,
                f"⚠️ **ERROR: Include file not found: {filename}**\n"
            )
            continue
        
        with open(include_path, 'r') as f:
            included_content = f.read()
        
        # Recursively resolve nested includes
        included_content = resolve_includes(included_content, base_dir, max_depth, _depth + 1)
        
        # Add a comment showing the source
        header = f"\n<!-- Included from: {filename} -->\n"
        footer = f"\n<!-- End include: {filename} -->\n"
        
        full_include = header + included_content + footer
        content = content.replace(directive, full_include)
    
    return content


def combine_markdown(input_file: str = "README.md", output_file: str = None) -> str:
    """Main function to combine markdown files."""
    base_dir = Path(input_file).parent.resolve()
    
    with open(input_file, 'r') as f:
        content = f.read()
    
    combined = resolve_includes(content, base_dir)
    
    if output_file:
        with open(output_file, 'w') as f:
            f.write(combined)
        print(f"✓ Combined markdown written to: {output_file}")
    
    return combined


def main():
    parser = argparse.ArgumentParser(description="Combine markdown files with includes")
    parser.add_argument("--input", "-i", default="README.md", help="Input file (default: README.md)")
    parser.add_argument("--output", "-o", default=None, help="Output file (default: {input}.combined.md)")
    args = parser.parse_args()
    
    if args.output is None:
        args.output = args.input.replace('.md', '.combined.md')
    
    combine_markdown(args.input, args.output)


if __name__ == "__main__":
    main()
