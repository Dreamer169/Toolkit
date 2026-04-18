import { Router, type IRouter } from "express";
import healthRouter from "./health";
import toolsRouter from "./tools";
import dataRouter from "./data.js";
import agentRouter from "./agent.js";
import cdpRelayRouter from "./cdp_relay.js";
import accountsRouter from "./accounts.js";
import gatewayRouter from "./gateway.js";

const router: IRouter = Router();

router.use(healthRouter);
router.use(cdpRelayRouter);
router.use(toolsRouter);
router.use(dataRouter);
router.use(agentRouter);
router.use(accountsRouter);
router.use(gatewayRouter);

export default router;
