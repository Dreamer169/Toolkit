import { Router, type IRouter } from "express";
import healthRouter from "./health";
import proxyRouter from "./proxy";
import cdpInfoRouter from "./cdp-info";
import cfWarmupRouter from "./cf-warmup";

const router: IRouter = Router();

router.use(healthRouter);
router.use(cdpInfoRouter);
router.use(cfWarmupRouter);
// proxyRouter 保留以兼容旧前端，但新架构 (CDP 截图流) 不再使用它
router.use(proxyRouter);

export default router;
