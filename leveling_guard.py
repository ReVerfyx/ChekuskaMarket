#!/usr/bin/env python3
"""Refund unavailable leveling orders and deactivate matching seller listings."""
import argparse
import logging
import os
import time

import bot

LOG = logging.getLogger('leveling-guard')
SKU = 'steal_egg_leveling'


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS leveling_failures (
        order_id TEXT PRIMARY KEY, refund_done INTEGER NOT NULL DEFAULT 0,
        lot_disabled INTEGER NOT NULL DEFAULT 0, refund_attempts INTEGER NOT NULL DEFAULT 0,
        attempted_at REAL NOT NULL DEFAULT 0, last_error TEXT)''')
    db.commit()


def enqueue(db, order_id):
    initialize(db)
    with db:
        db.execute('INSERT OR IGNORE INTO leveling_failures(order_id) VALUES (?)', (order_id,))


def process(db, account, *, dry_run=False):
    initialize(db)
    item = next(x for x in bot.catalog() if x['sku'] == SKU)
    for row in db.execute('SELECT * FROM leveling_failures WHERE refund_done=0 OR lot_disabled=0').fetchall():
        order_id = row['order_id']
        try:
            order = account.get_order(order_id)
            if str(order.seller_id) != str(account.id) or order.short_description != item['match']:
                raise ValueError('SellerOrTitleMismatch')
        except Exception as exc:
            with db:
                db.execute('UPDATE leveling_failures SET last_error=? WHERE order_id=?', (type(exc).__name__, order_id))
            continue
        if dry_run:
            print(f'[DRY_RUN] refund {order_id}; deactivate matching leveling lots')
            continue
        # Both actions are independent: a failed delisting must not block the refund.
        if not row['lot_disabled']:
            try:
                lots = [x for x in account.get_user(account.id).get_lots() if x.description == item['match']]
                for lot in lots:
                    fields = account.get_lot_fields(int(lot.id))
                    if fields.title_ru != item['match']:
                        raise ValueError('LotTitleMismatch')
                    fields.active = False
                    account.save_lot(fields)
                    if account.get_lot_fields(int(lot.id)).active:
                        raise ValueError('LotStillActive')
                # No matching public lot also means it is not offered for sale.
                with db:
                    db.execute('UPDATE leveling_failures SET lot_disabled=1 WHERE order_id=?', (order_id,))
            except Exception as exc:
                LOG.warning('Delisting pending for %s: %s', order_id, type(exc).__name__)
        if not row['refund_done']:
            try:
                status = getattr(order.status, 'name', '')
                if status == 'REFUNDED':
                    with db:
                        db.execute('UPDATE leveling_failures SET refund_done=1,last_error=NULL WHERE order_id=?', (order_id,))
                elif status == 'PAID':
                    if row['refund_attempts'] >= 3:
                        raise ValueError('RefundNeedsManualReview')
                    if time.time() - row['attempted_at'] < 60:
                        continue
                    # Persist before the request. After timeout, read platform status first.
                    with db:
                        db.execute('UPDATE leveling_failures SET refund_attempts=refund_attempts+1,attempted_at=? WHERE order_id=?', (time.time(), order_id))
                    account.refund(order_id)
                    if getattr(account.get_order(order_id).status, 'name', '') == 'REFUNDED':
                        with db:
                            db.execute('UPDATE leveling_failures SET refund_done=1,last_error=NULL WHERE order_id=?', (order_id,))
                else:
                    raise ValueError('OrderNotPaidOrRefunded')
            except Exception as exc:
                with db:
                    db.execute('UPDATE leveling_failures SET last_error=? WHERE order_id=?', (str(exc) if isinstance(exc, ValueError) else type(exc).__name__, order_id))
                LOG.warning('Refund pending for %s: %s', order_id, type(exc).__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    from FunPayAPI import Account
    key = os.environ.get('FUNPAY_GOLDEN_KEY')
    if not key:
        raise SystemExit('Укажите FUNPAY_GOLDEN_KEY в .env')
    logging.basicConfig(level=logging.INFO)
    # One guard process per database; do not race financial actions.
    import fcntl
    db = bot.database()
    lock = open(bot.DB_PATH.parent / 'leveling-guard.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    account = Account(key).get()
    refreshed = time.monotonic()
    while True:
        try:
            if time.monotonic() - refreshed > 2400:
                account.get()
                refreshed = time.monotonic()
            process(db, account, dry_run=bot.DRY_RUN)
        except Exception as exc:
            LOG.error('Guard retry: %s', type(exc).__name__)
        if args.once:
            return
        time.sleep(15)


if __name__ == '__main__':
    main()
