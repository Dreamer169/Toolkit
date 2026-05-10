// Extended navigator probe: check properties near the 2 remaining PixelScan Detected items
const src = `
(async () => {
  const n = navigator;
  const result = {};

  // After-Plugins candidates (item #31)
  result.pdfViewerEnabled = n.pdfViewerEnabled;
  result.scheduling_exists = 'scheduling' in n;
  result.scheduling_isInputPending = n.scheduling ? typeof n.scheduling.isInputPending : 'N/A';
  result.storage_exists = 'storage' in n;
  result.storage_estimate = n.storage ? typeof n.storage.estimate : 'N/A';
  result.locks_exists = 'locks' in n;
  result.credentials_exists = 'credentials' in n;
  result.serviceWorker_exists = 'serviceWorker' in n;
  result.mediaSession_exists = 'mediaSession' in n;
  result.bluetooth_exists = 'bluetooth' in n;
  result.usb_exists = 'usb' in n;
  result.serial_exists = 'serial' in n;
  result.hid_exists = 'hid' in n;
  result.wakeLock_exists = 'wakeLock' in n;
  result.xr_exists = 'xr' in n;
  result.gpu_exists = 'gpu' in n;

  // Near-ClearAppBadge candidates (item #69)
  result.clearAppBadge_exists = 'clearAppBadge' in n;
  result.setAppBadge_exists = 'setAppBadge' in n;
  result.getInstalledRelatedApps_exists = 'getInstalledRelatedApps' in n;
  result.userActivation_exists = 'userActivation' in n;
  result.userActivation_isActive = n.userActivation ? n.userActivation.isActive : 'N/A';
  result.userActivation_hasBeenActive = n.userActivation ? n.userActivation.hasBeenActive : 'N/A';
  result.userAgentData_exists = 'userAgentData' in n;
  result.globalPrivacyControl = n.globalPrivacyControl;
  result.connection_exists = 'connection' in n;
  result.connection_downlink = n.connection ? n.connection.downlink : 'N/A';
  result.windowControlsOverlay_exists = 'windowControlsOverlay' in n;
  result.ink_exists = 'ink' in n;
  result.virtualKeyboard_exists = 'virtualKeyboard' in n;
  result.joinAdInterestGroup_exists = 'joinAdInterestGroup' in n;
  result.runAdAuction_exists = 'runAdAuction' in n;
  result.deprecatedRunAdAuctionEnforcesKAnonymity_exists = 'deprecatedRunAdAuctionEnforcesKAnonymity' in n;

  // Native-code check for setAppBadge/clearAppBadge descriptors
  try {
    const desc = Object.getOwnPropertyDescriptor(n.__proto__, 'clearAppBadge');
    result.clearAppBadge_desc_native = desc ? String(desc.value || desc.get).includes('[native code]') : 'no-desc';
    const desc2 = Object.getOwnPropertyDescriptor(n.__proto__, 'setAppBadge');
    result.setAppBadge_desc_native = desc2 ? String(desc2.value || desc2.get).includes('[native code]') : 'no-desc';
  } catch(e) { result.badge_desc_err = String(e); }

  console.log(JSON.stringify(result, null, 2));
})();
`;

// Run via playwright
import { chromium } from 'playwright';
const br = await chromium.connect('ws://127.0.0.1:19870');
try {
  const ctx = await br.newContext();
  const p = await ctx.newPage();
  const r = await p.evaluate(new Function(src.slice(src.indexOf('(async'))));
  console.log(JSON.stringify(r, null, 2));
  await ctx.close();
} finally { await br.close(); }
