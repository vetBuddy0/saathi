// The family app's service worker: the only part of the app that runs
// while the app is closed, so the only part that can make the phone
// "ring". A push (call/push.py, encrypted to this phone) becomes a
// notification that stays until it is acted on; tapping it opens the
// call. A web page cannot play a ringtone while closed -- the
// notification's vibration and the phone's own notification sound are
// the ring.
//
// The notification's URL comes from the push, not from this worker's
// origin: with a quick tunnel the device's hostname changes on restart,
// and the push carries the current one plus a per-call token, so a ring
// still opens a page that can answer it (call/family_server.py).

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function parse(event) {
  try {
    return event.data ? event.data.json() : null;
  } catch (err) {
    return null;
  }
}

self.addEventListener("push", (event) => {
  const data = parse(event);
  if (!data) return;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) client.postMessage({ type: "push", data });
      if (data.kind === "ring") {
        await self.registration.showNotification(data.title, {
          body: data.body,
          tag: data.call_id,
          renotify: true,
          requireInteraction: true,
          vibrate: [600, 300, 600, 300, 600, 300, 600, 300, 600],
          icon: "/family/static/icon-192.png",
          data: { url: data.url, call_id: data.call_id },
          actions: [
            { action: "answer", title: "Answer" },
            { action: "decline", title: "Decline" },
          ],
        });
        return;
      }
      for (const old of await self.registration.getNotifications({ tag: data.call_id })) {
        old.close();
      }
      if (data.kind === "missed") {
        await self.registration.showNotification(data.title, {
          body: data.body,
          tag: `missed-${data.call_id}`,
          icon: "/family/static/icon-192.png",
          data: { url: "/family/" },
        });
      }
    })()
  );
});

function fragmentParams(url) {
  try {
    return new URLSearchParams(new URL(url).hash.slice(1));
  } catch (err) {
    return new URLSearchParams();
  }
}

self.addEventListener("notificationclick", (event) => {
  const notification = event.notification;
  const url = (notification.data && notification.data.url) || "/family/";
  notification.close();
  if (event.action === "decline") {
    const params = fragmentParams(url);
    const origin = new URL(url, self.location.href).origin;
    event.waitUntil(
      fetch(`${origin}/family/api/decline`, {
        method: "POST",
        // text/plain: a "simple" request, so no CORS preflight when the
        // device's hostname has changed since this worker was installed.
        headers: { "Content-Type": "text/plain" },
        body: JSON.stringify({ call_id: params.get("call"), call_token: params.get("t") }),
      }).catch(() => {})
    );
    return;
  }
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const target = new URL(url, self.location.href);
      for (const client of windows) {
        if (new URL(client.url).origin === target.origin && "focus" in client) {
          client.postMessage({ type: "open", url: target.href });
          return client.focus();
        }
      }
      return self.clients.openWindow(target.href);
    })()
  );
});
