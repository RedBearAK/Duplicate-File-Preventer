#!/bin/bash

# Ensure that required dependencies are loaded into Python virtual environment.
# Python venv must be activated before this script is sourced or executed.


# Define dependencies as "import_name:pip_name" pairs
# If import and pip names are the same, just use "package_name"
REQUIRED_DEPS=(
    "rich"
    "watchdog" 
)

# Function to check and install missing dependencies
install_missing_deps() {
    local missing_packages=()
    
    # Check each dependency
    for dep_pair in "${REQUIRED_DEPS[@]}"; do
        # Split on colon to get import_name and pip_name
        IFS=':' read -r import_name pip_name <<< "$dep_pair"
        
        # If no pip_name specified, use import_name
        if [ -z "$pip_name" ]; then
            pip_name="$import_name"
        fi
        
        # Test if package can be imported
        if ! python -c "import ${import_name}" 2>/dev/null; then
            missing_packages+=("${pip_name}")
        fi
    done
    
    # Install missing dependencies
    if [ ${#missing_packages[@]} -gt 0 ]; then
        echo "Installing missing Python dependencies: ${missing_packages[*]}"
        pip install "${missing_packages[@]}"
    else
        echo "All required Python dependencies are already installed."
    fi
}

# Call the function
install_missing_deps
