#!/bin/bash

# Define color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Define Home Assistant config directory
if [ -z "$HASSPATH" ]; then
    if [ -d "/config" ]; then
        HASSPATH="/config"
    else
        HASSPATH="$HOME/.homeassistant"
    fi
fi

# Print welcome message
echo -e "${GREEN}Installing Marstek Energy Integration...${NC}"

# Check if target directory exists
COMPONENT_PATH="${HASSPATH}/custom_components"
if [ ! -d "$COMPONENT_PATH" ]; then
    echo -e "${RED}Creating custom_components directory...${NC}"
    mkdir -p "$COMPONENT_PATH"
fi

# Copy integration files
echo -e "${GREEN}Copying integration files to ${COMPONENT_PATH}...${NC}"
cp -r ../custom_components/marstek "${COMPONENT_PATH}/"

# Check if copy was successful
if [ $? -eq 0 ]; then
    echo -e "${GREEN}Installation successful!${NC}"
    echo -e "${GREEN}Please restart Home Assistant to apply the changes.${NC}"
else
    echo -e "${RED}Installation failed! Please check permissions and paths.${NC}"
    exit 1
fi
