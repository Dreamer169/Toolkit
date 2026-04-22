import { Router, type IRouter } from "express";

const router: IRouter = Router();

router.get("/cdp/info", (_req, res) => {
  res.json({
    transport: "websocket",
    path: "/api/cdp/ws",
    note: "Use ws:// or wss:// upgrade. Send {type:'navigate',url} to start; receive {type:'frame',data:base64jpeg}",
  });
});

export default router;
