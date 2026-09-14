#!/usr/bin/env python3
"""Compatibility entrypoint; defaults to validation without database writes."""
from load_sales_snapshot import main

if __name__ == "__main__":
    main()
