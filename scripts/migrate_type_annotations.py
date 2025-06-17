#!/usr/bin/env python3
"""
Script to migrate comment-based type annotations to inline type annotations according to PEP 484.

This script helps automate the conversion of the Sentry Python SDK codebase from
comment-based type annotations (# type: ...) to inline type annotations.
"""

import re
import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional


class TypeAnnotationMigrator:
    def __init__(self):
        # Patterns for different type annotation formats
        self.function_signature_pattern = re.compile(
            r'def\s+(\w+)\s*\([^)]*\):\s*\n\s*#\s*type:\s*\([^)]*\)\s*->\s*([^#\n]+)'
        )
        
        self.parameter_pattern = re.compile(
            r'(\w+),?\s*#\s*type:\s*([^#\n]+)'
        )
        
        self.variable_annotation_pattern = re.compile(
            r'(\w+)\s*=\s*([^#\n]+?)\s*#\s*type:\s*([^#\n]+)'
        )
        
        self.simple_function_pattern = re.compile(
            r'def\s+(\w+)\s*\([^)]*\):\s*\n\s*#\s*type:\s*\([^)]*\)\s*->\s*([^#\n]+)'
        )

    def migrate_file(self, file_path: Path) -> Tuple[bool, str]:
        """
        Migrate a single file from comment-based to inline type annotations.
        
        Returns:
            Tuple of (success: bool, error_message: str)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # Process the content line by line for more precise control
            lines = content.splitlines()
            modified_lines = []
            i = 0
            
            while i < len(lines):
                line = lines[i]
                
                # Skip lines that are already using inline annotations
                if ' -> ' in line and 'def ' in line:
                    modified_lines.append(line)
                    i += 1
                    continue
                
                # Handle function definitions with comment-based return types
                if line.strip().startswith('def ') and ':' in line:
                    # Look ahead for comment-based type annotation
                    if i + 1 < len(lines) and '# type:' in lines[i + 1]:
                        type_comment = lines[i + 1].strip()
                        
                        # Extract return type from comment
                        if ' -> ' in type_comment:
                            return_type = type_comment.split(' -> ')[1].strip()
                            # Remove the comment line and add return type to function def
                            if not line.rstrip().endswith(' -> None'):
                                if line.rstrip().endswith(':'):
                                    line = line.rstrip()[:-1] + f' -> {return_type}:'
                                else:
                                    line = line.rstrip() + f' -> {return_type}'
                            modified_lines.append(line)
                            i += 2  # Skip both the function line and the comment line
                            continue
                
                # Handle variable annotations
                variable_match = self.variable_annotation_pattern.search(line)
                if variable_match:
                    var_name = variable_match.group(1)
                    var_value = variable_match.group(2).strip()
                    var_type = variable_match.group(3).strip()
                    
                    # Convert to inline annotation
                    new_line = f'{var_name}: {var_type} = {var_value}'
                    modified_lines.append(new_line)
                    i += 1
                    continue
                
                # Default: keep the line as is
                modified_lines.append(line)
                i += 1
            
            new_content = '\n'.join(modified_lines)
            
            # Only write if content changed
            if new_content != original_content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                return True, ""
            else:
                return True, "No changes needed"
                
        except Exception as e:
            return False, str(e)

    def find_files_with_type_comments(self, root_dir: Path) -> List[Path]:
        """Find all Python files that contain comment-based type annotations."""
        files_with_comments = []
        
        for py_file in root_dir.rglob('*.py'):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if '# type:' in content:
                        files_with_comments.append(py_file)
            except Exception:
                # Skip files that can't be read
                continue
                
        return files_with_comments

    def get_migration_stats(self, root_dir: Path) -> Dict[str, int]:
        """Get statistics about the migration needs."""
        stats = {
            'total_files': 0,
            'files_with_type_comments': 0,
            'function_type_comments': 0,
            'variable_type_comments': 0,
            'parameter_type_comments': 0
        }
        
        for py_file in root_dir.rglob('*.py'):
            stats['total_files'] += 1
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                    if '# type:' in content:
                        stats['files_with_type_comments'] += 1
                        
                        # Count different types of annotations
                        stats['function_type_comments'] += len(
                            re.findall(r'def\s+\w+[^:]*:\s*\n\s*#\s*type:', content)
                        )
                        stats['variable_type_comments'] += len(
                            re.findall(r'\w+\s*=\s*[^#]*#\s*type:', content)
                        )
                        stats['parameter_type_comments'] += len(
                            re.findall(r'\w+,?\s*#\s*type:', content)
                        )
                        
            except Exception:
                continue
                
        return stats


def main():
    """Main function to run the migration."""
    if len(sys.argv) > 1:
        root_dir = Path(sys.argv[1])
    else:
        root_dir = Path('.')
    
    if not root_dir.exists():
        print(f"Error: Directory {root_dir} does not exist")
        sys.exit(1)
    
    migrator = TypeAnnotationMigrator()
    
    # Get migration statistics
    print("Analyzing codebase for type annotation migration...")
    stats = migrator.get_migration_stats(root_dir)
    
    print(f"Migration Statistics:")
    print(f"  Total Python files: {stats['total_files']}")
    print(f"  Files with type comments: {stats['files_with_type_comments']}")
    print(f"  Function type comments: {stats['function_type_comments']}")
    print(f"  Variable type comments: {stats['variable_type_comments']}")
    print(f"  Parameter type comments: {stats['parameter_type_comments']}")
    print()
    
    # Find files that need migration
    files_to_migrate = migrator.find_files_with_type_comments(root_dir)
    
    if not files_to_migrate:
        print("No files found that need type annotation migration.")
        return
    
    print(f"Found {len(files_to_migrate)} files that need migration:")
    for file_path in files_to_migrate[:10]:  # Show first 10
        print(f"  {file_path}")
    if len(files_to_migrate) > 10:
        print(f"  ... and {len(files_to_migrate) - 10} more files")
    print()
    
    # Ask for confirmation
    response = input("Do you want to proceed with the migration? (y/N): ")
    if response.lower() != 'y':
        print("Migration cancelled.")
        return
    
    # Perform migration
    successful = 0
    failed = 0
    
    for file_path in files_to_migrate:
        success, error = migrator.migrate_file(file_path)
        if success:
            successful += 1
            print(f"✓ Migrated: {file_path}")
        else:
            failed += 1
            print(f"✗ Failed: {file_path} - {error}")
    
    print(f"\nMigration completed:")
    print(f"  Successfully migrated: {successful} files")
    print(f"  Failed: {failed} files")
    
    if failed > 0:
        print("\nNote: Some files may require manual review and migration.")


if __name__ == '__main__':
    main()