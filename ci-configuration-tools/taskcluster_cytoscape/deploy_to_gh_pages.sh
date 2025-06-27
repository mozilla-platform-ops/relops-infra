#!/usr/bin/env bash

set -e
set -x

# this only handles updates... initial deployment needs to do symlink work

GH_PAGES_REPO_PATH="$HOME/git/gh_pages_test/taskcluster_cytoscape_viewer/"

# copy worker_pools_images.cyto.json to GH_PAGES_REPO_PATH
cp worker_pools_images.cyto.json $GH_PAGES_REPO_PATH

# copy worker_pools_viewer_focusmode_multiselect.html to GH_PAGES_REPO_PATH
# cp cytoscape_viewer/worker_pools_viewer_focusmode_multiselect.html $GH_PAGES_REPO_PATH
# copy to easier path
cp cytoscape_viewer/worker_pools_viewer_focusmode_multiselect.html $GH_PAGES_REPO_PATH/viewer.html


cd $GH_PAGES_REPO_PATH
# commit changes
git add worker_pools_images.cyto.json viewer.html
# check if there are changes before committing
if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to commit."
  exit 0
fi
git commit -m "Update worker pools viewer files"
# push changes
git push origin main