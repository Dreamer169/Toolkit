import { Router, type IRouter } from "express";
import healthRouter from "./health";
import toolsRouter from "./tools";
import dataRouter from "./data.js";

const router: IRouter = Router();

router.use(healthRouter);
router.use(toolsRouter);
router.use(dataRouter);

export default router;
