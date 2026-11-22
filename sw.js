// 台股掃描 App 的背景程式：收推播、點通知開啟盤中訊息
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "選股雷達", {
    body: d.body || "",
    tag: d.tag || "tws",
    icon: "icon-192.png",
    badge: "icon-192.png",
    data: { url: "./#alerts" },
  }));
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
