# Copyright 2026 Camptocamp SA
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from datetime import datetime

from .common import TestDdmrpCommon


class TestDdmrpNfpPartialRefreshOrdering(TestDdmrpCommon):
    """A supply covering the demand must prevent the auto procurement.

    In production a customer pick leaves the buffer location and a supply
    enters that same buffer location a few seconds later. Each move triggers
    ``stock.move._update_ddmrp_nfp``, which queues one partial refresh per
    side: ``cron_actions(only_nfp="out")`` for the demand, then
    ``cron_actions(only_nfp="in")`` for the supply. The demand refresh always
    wins the race, and it recomputes the net flow position from the *stored*
    ``incoming_dlt_qty``, which still ignores the supply. The buffer therefore
    sees a stockout that does not exist and orders the very quantity that is
    already on its way.
    """

    def test_no_auto_procure_when_demand_refreshed_before_supply(self):
        buffer = self.buffer_purchase
        # The buffer watches WH/Stock; ``binA`` is one of its sublocations, so
        # a move out of it is demand for the buffer and a move into it supply.
        self.assertTrue(self.binA.is_sublocation_of(buffer.location_id))

        buffer.auto_procure = True
        buffer.auto_procure_option = "stockout"

        # Nightly full refresh: nothing demanded, nothing incoming, no order.
        buffer.cron_actions()
        self.assertEqual(buffer.qualified_demand, 0.0)
        self.assertEqual(buffer.incoming_dlt_qty, 0.0)
        self.assertEqual(buffer.net_flow_position, 0.0)
        self.assertFalse(
            self.pol_model.search([("product_id", "=", self.product_purchased.id)])
        )

        # Both moves must exist before either partial refresh runs: in
        # production the two refreshes are queue jobs, so they are executed
        # after both moves have been created. Turning the automatic update off
        # keeps the stored net flow position untouched until we run the
        # refreshes ourselves, in the order production runs them.
        self.main_company.ddmrp_auto_update_nfp = False
        date_move = datetime.today()
        qty = 10.0
        # The customer pick: demand leaving the buffer location.
        picking_out = self.create_picking_out(self.product_purchased, date_move, qty)
        # The replenishment triggered elsewhere: supply entering the buffer
        # location, for exactly the quantity that just left.
        picking_in = self.create_picking_in(self.product_purchased, date_move, qty)

        # Both moves are indeed seen by the buffer, one on each side.
        out_buffers, _in_buffers = picking_out.move_ids._find_buffers_to_update_nfp()
        self.assertIn(buffer, out_buffers)
        _out_buffers, in_buffers = picking_in.move_ids._find_buffers_to_update_nfp()
        self.assertIn(buffer, in_buffers)

        # The production order: demand side first, supply side second.
        buffer.cron_actions(only_nfp="out")
        buffer.cron_actions(only_nfp="in")

        self.assertEqual(buffer.qualified_demand, qty)
        self.assertEqual(buffer.incoming_dlt_qty, qty)
        self.assertEqual(buffer.net_flow_position, 0.0)
        pol = self.pol_model.search([("product_id", "=", self.product_purchased.id)])
        self.assertFalse(
            pol,
            "The buffer replenished although the incoming supply already "
            "covers the demand that left the buffer location.",
        )
