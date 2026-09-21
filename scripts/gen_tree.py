import os

def generate_tree(dir_path, prefix=""):
    exclude_dirs = {'.git', '__pycache__', '.venv', 'node_modules', '.pytest_cache', 'frontend', 'models'}
    # Also excluding 'frontend' node_modules and big folders so it's not unreadable, although user said "entire structure skeleton of this project including the files". 
    # Let me only exclude the massive cache/build folders.
    exclude_dirs = {'.git', '__pycache__', '.venv', 'node_modules', '.pytest_cache'}
    paths = []
    
    try:
        entries = sorted([e for e in os.listdir(dir_path) if e not in exclude_dirs])
    except PermissionError:
        return paths

    for i, entry in enumerate(entries):
        full_path = os.path.join(dir_path, entry)
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        
        paths.append(f"{prefix}{connector}{entry}")
        
        if os.path.isdir(full_path):
            extension = "    " if is_last else "│   "
            paths.extend(generate_tree(full_path, prefix + extension))
            
    return paths

tree_lines = generate_tree(".")
with open("tree_output.txt", "w", encoding="utf-8") as f:
    f.write("Kuro-LLM/\n")
    f.write("\n".join(tree_lines))
