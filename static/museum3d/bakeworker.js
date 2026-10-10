/* The museum's light baker, off the page's main thread: rooms arrive as
   plans, leave as typed arrays (compute.js), one at a time, in the order
   the renderer asks for them. */

import { computeRoom, transferList } from "./compute.js";

self.onmessage = (e) => {
  const { id, room, opts, stair } = e.data;
  try {
    const piece = computeRoom(room, opts, stair);
    self.postMessage({ id, piece }, transferList(piece));
  } catch (err) {
    self.postMessage({ id, error: String((err && err.stack) || err) });
  }
};
