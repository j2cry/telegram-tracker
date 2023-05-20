#!/bin/bash
if [ ! -z "$1" ]; then
    echo $(($(cat .buildno) + 1)) > .buildno
fi
