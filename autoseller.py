#!/usr/bin/env python3
"""Lot maintenance process: only auto-raise existing FunPay lots."""
import argparse
import logging
import os
import time

import bot


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="ChekuskaMarket lot maintenance")
    parser.add_argument("mode", choices=("once", "loop", "sections"))
    args = parser.parse_args()
    account = bot.connect_account()
    if args.mode == "sections":
        bot.list_sections(account)
        return
    while True:
        account.get()
        bot.raise_lots_once(account)
        if args.mode == "once":
            return
        time.sleep(max(60, int(os.getenv("RAISE_INTERVAL_SECONDS", "3600"))))


if __name__ == "__main__":
    main()
