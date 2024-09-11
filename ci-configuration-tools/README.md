# ci-configuration-tools

## Overview

Various tools that make inspecting ci-configuration's worker pools and images.

The generated json from `ci-config generate` isn't what we want (we want the unresolved image alias names vs the fully resolved paths).

## Installation

```
./install.sh PATH_TO_YOUR_CI_CONFIGURATION_CLIENT
```

## Usage

Python scripts have self-explanatory help and usage (via -h or --help).

Bash scripts are named logically and should be simple enough to see what they're doing.
