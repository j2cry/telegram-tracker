#!/bin/bash
FILENAME=__version__.py
BUILDNO=$(($( sed -E 's/.*[a-z]([0-9]+)./\1/' $FILENAME )))
# increase buildno
if [ -z "$1" ]; then
    ((BUILDNO++))
fi
echo "Build no: "$BUILDNO
sed -Ei 's/([a-z])[0-9]+/\1'$BUILDNO'/' $FILENAME
# build
# ( your build code here ) || \
# (sed -Ei 's/([a-z])[0-9]+/\1'$(($BUILDNO - 1))'/' $FILENAME \
#     && echo "Build number was NOT incremented due to error above.")
