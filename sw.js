// 台股掃描 App 的背景程式：收推播、點通知開啟盤中訊息
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// 主畫面圖示上的未讀數字：每收到一則推播 +1，打開盤中訊息頁時由網頁歸零
async function bumpBadge() {
  try {
    const c = await caches.open("tws-badge");
    const r = await c.match("n");
    const n = (r ? parseInt(await r.text(), 10) || 0 : 0) + 1;
    await c.put("n", new Response(String(n)));
    if (self.navigator.setAppBadge) await self.navigator.setAppBadge(n);
  } catch (err) {}
}

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(Promise.all([
    self.registration.showNotification(d.title || "選股雷達", {
      body: d.body || "",
      tag: d.tag || "tws",
      icon: "icon-192.png",
      badge: "icon-192.png",
      data: { url: "./#alerts" },
    }),
    bumpBadge(),
  ]));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "./#alerts", self.registration.scope).href;
  e.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of list) {
      if (c.url.startsWith(self.registration.scope)) {
        await c.focus();
        c.postMessage({ type: "open-alerts" });
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
