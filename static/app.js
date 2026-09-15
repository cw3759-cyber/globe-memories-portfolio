/* 我们的地球 —— 前端主逻辑 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const R = window.GM_ROOT || "";

const state = {
  me: null,
  members: [],
  places: [],
  selected: null,
  draft: null, // 正在添加的新地点坐标
  editing: null, // 正在编辑的地点
  chatRef: null, // 留言关联的地点
  lastMsgId: 0,
  unread: 0,
  shareUrl: "",
  files: [],
  lightbox: { photos: [], index: 0 },
  geo: { countries: [], provinces: [], cities: [] },
  mapLabels: [],
  searchHits: [],
  bloomKey: null,
  bloomPinned: false,
  bloomLeaveTimer: 0,
  bloomRaf: 0,
};

// --------------------------------------------------------------------------
// 基础工具
// --------------------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(R + path, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    location.href = R + "/login";
    throw new Error("未登录");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `请求失败 (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

const apiJSON = (path, method, body) =>
  api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 2600);
}

function initials(name) {
  return (name || "?").trim().slice(0, 1).toUpperCase();
}

function fmtDate(value) {
  if (!value) return "";
  const d = new Date(value.length <= 10 ? `${value}T00:00:00` : `${value}Z`);
  if (Number.isNaN(d.getTime())) return value;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function fmtTime(value) {
  const d = new Date(`${value}Z`);
  if (Number.isNaN(d.getTime())) return value;
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

function escapeHTML(text) {
  return String(text ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

// --------------------------------------------------------------------------
// 地球
// --------------------------------------------------------------------------

const globe = Globe()
  .globeImageUrl(R + "/static/img/earth-blue-marble.jpg")
  .bumpImageUrl(R + "/static/img/earth-topology.png")
  .backgroundImageUrl(R + "/static/img/night-sky.png")
  .atmosphereColor("#6ea8ff")
  .atmosphereAltitude(0.19)
  .htmlLat("lat")
  .htmlLng("lng")
  .htmlAltitude((d) => (d.overlay === "label" ? 0.008 : 0.014))
  .htmlElement(overlayElement)
  .polygonCapColor(() => "rgba(190, 215, 255, 0.04)")
  .polygonSideColor(() => "rgba(0, 0, 0, 0)")
  .polygonStrokeColor(() => "rgba(255, 255, 255, 0.34)")
  .polygonAltitude(0.002)
  .polygonsTransitionDuration(0)
  .ringColor(() => "#ffb703")
  .ringMaxRadius(4)
  .ringPropagationSpeed(1.6)
  .ringRepeatPeriod(700)
  .onGlobeClick(onGlobeClick)
  .onPolygonHover((feat) => {
    state.hoverCountry = feat && feat.properties ? feat.properties.name : null;
    if (altitude() > 1.7 && state.hover) {
      showHoverName(state.hoverCountry, state.hover.x, state.hover.y);
    }
  })
  .onPolygonClick((_p, _ev, coords) => coords && onGlobeClick(coords))(document.getElementById("globe"));

const controls = globe.controls();
controls.autoRotate = true;
controls.autoRotateSpeed = 0.32;
controls.enableDamping = true;
controls.enableZoom = true;
controls.zoomSpeed = 1.15;
controls.minDistance = 120;
controls.maxDistance = 520;

window.addEventListener("resize", () => {
  globe.width(window.innerWidth).height(window.innerHeight);
});

function overlayElement(item) {
  if (item.overlay === "label") return geoLabelElement(item);
  return pinElement(item);
}

function geoLabelElement(item) {
  const el = document.createElement("div");
  el.className = `geo-label ${item.layer || ""}`;
  el.textContent = item.name;
  return el;
}

function haversineKm(a, b) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const sin =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(sin)));
}

// 地球上看着是同一个点的回忆合成一个气泡（荆州几个区大约几公里，都会并进来）
const CLUSTER_KM = 15;

function clusterPlaces() {
  const places = state.places;
  if (!places.length) return [];
  const parent = places.map((_, i) => i);
  const find = (i) => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };
  for (let i = 0; i < places.length; i++) {
    for (let j = i + 1; j < places.length; j++) {
      if (haversineKm(places[i], places[j]) > CLUSTER_KM) continue;
      const a = find(i);
      const b = find(j);
      if (a !== b) parent[a] = b;
    }
  }
  const groups = new Map();
  places.forEach((place, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(place);
  });
  return [...groups.values()].map((group) => {
    const sorted = [...group].sort(
      (a, b) => String(a.date || "").localeCompare(String(b.date || "")) || a.id - b.id
    );
    return {
      lat: group.reduce((s, p) => s + Number(p.lat), 0) / group.length,
      lng: group.reduce((s, p) => s + Number(p.lng), 0) / group.length,
      places: sorted,
      clusterKey: `g:${sorted.map((p) => p.id).join("-")}`,
    };
  });
}

function placesForKey(key) {
  const cluster = clusterPlaces().find((c) => c.clusterKey === key);
  return cluster ? cluster.places : [];
}

function pinElByKey(key) {
  return [...document.querySelectorAll(".pin")].find((el) => el.dataset.key === key);
}

let swallowGlobeClickUntil = 0;
function swallowGlobeClick() {
  swallowGlobeClickUntil = performance.now() + 450;
}

function petalOffsets(n) {
  const radius = 108 + Math.min(n, 8) * 8;
  if (n === 2) {
    return [
      { x: -118, y: -36, tilt: -7 },
      { x: 118, y: -36, tilt: 7 },
    ];
  }
  return Array.from({ length: n }, (_, i) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return {
      x: Math.cos(a) * radius,
      y: Math.sin(a) * radius * 0.88 - 6,
      tilt: Math.cos(a) * 8,
    };
  });
}

function bloomStillHovered() {
  return Boolean(
    document.querySelector(".pin.blooming:hover") ||
      document.querySelector("#memory-bloom .bloom-hotzone:hover") ||
      document.querySelector("#memory-bloom .memory-petal:hover")
  );
}

function syncBloomAnchor() {
  const anchor = $("#memory-bloom .bloom-anchor");
  const pin = pinElByKey(state.bloomKey);
  if (!anchor || !pin) return;
  const box = pin.getBoundingClientRect();
  if (box.width < 2 && box.height < 2) return;
  anchor.style.left = `${box.left + box.width / 2}px`;
  anchor.style.top = `${box.top + 10}px`;
}

function startBloomTrack() {
  cancelAnimationFrame(state.bloomRaf);
  const tick = () => {
    if (!state.bloomKey) return;
    syncBloomAnchor();
    state.bloomRaf = requestAnimationFrame(tick);
  };
  tick();
}

function bloomOnPin(el, { pin = false } = {}) {
  if (!el) return;
  const key = el.dataset.key;
  const places = placesForKey(key);
  if (places.length < 2) return;
  pauseRotation();
  state.bloomKey = key;
  if (pin) state.bloomPinned = true;
  document.querySelectorAll(".pin.blooming").forEach((p) => p.classList.remove("blooming"));
  el.classList.add("blooming");

  const layer = $("#memory-bloom");
  layer.hidden = false;
  layer.classList.remove("out");
  layer.innerHTML = "";
  const anchor = document.createElement("div");
  anchor.className = "bloom-anchor";
  const hot = document.createElement("div");
  hot.className = "bloom-hotzone";
  hot.addEventListener("pointerenter", () => clearTimeout(state.bloomLeaveTimer));
  hot.addEventListener("pointerleave", scheduleCollapseBloom);
  anchor.appendChild(hot);
  const hint = document.createElement("div");
  hint.className = "bloom-hint";
  hint.textContent = `${places.length} 段回忆，点一张看看`;
  anchor.appendChild(hint);
  const offs = petalOffsets(places.length);
  places.forEach((place, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "memory-petal";
    btn.style.setProperty("--i", String(i));
    btn.style.setProperty("--dx", `${offs[i].x}px`);
    btn.style.setProperty("--dy", `${offs[i].y}px`);
    btn.style.setProperty("--tilt", `${offs[i].tilt}deg`);
    const cover = place.photos && place.photos[0];
    btn.innerHTML = cover
      ? `<img src="${cover.thumb}" alt="" /><span>${escapeHTML(place.title)}</span>`
      : `<div class="ph">📍</div><span>${escapeHTML(place.title)}</span>`;
    btn.title = `${place.title}${place.date ? ` · ${fmtDate(place.date)}` : ""}`;
    btn.addEventListener("pointerenter", () => clearTimeout(state.bloomLeaveTimer));
    btn.addEventListener("pointerleave", scheduleCollapseBloom);
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      swallowGlobeClick();
      collapseBloom();
      selectPlace(place.id);
    });
    anchor.appendChild(btn);
  });
  layer.appendChild(anchor);
  syncBloomAnchor();
  startBloomTrack();
}

function revealCluster(el, { pin = false } = {}) {
  if (!el) return;
  const places = placesForKey(el.dataset.key);
  if (places.length > 7) {
    renderClusterModal(places);
    return;
  }
  bloomOnPin(el, { pin });
}

function scheduleCollapseBloom() {
  if (state.bloomPinned) return;
  clearTimeout(state.bloomLeaveTimer);
  state.bloomLeaveTimer = setTimeout(() => {
    if (bloomStillHovered()) return;
    collapseBloom();
  }, 480);
}

function collapseBloom() {
  clearTimeout(state.bloomLeaveTimer);
  cancelAnimationFrame(state.bloomRaf);
  const layer = $("#memory-bloom");
  if (!state.bloomKey && (!layer || layer.hidden)) return;
  if (layer) layer.classList.add("out");
  document.querySelectorAll(".pin.blooming").forEach((el) => el.classList.remove("blooming"));
  state.bloomKey = null;
  state.bloomPinned = false;
  setTimeout(() => {
    if (!layer || state.bloomKey) return;
    layer.hidden = true;
    layer.classList.remove("out");
    layer.innerHTML = "";
  }, 280);
}

function pinElement(item) {
  const places = item.places || [item];
  const place = places[0];
  const stacked = places.length > 1;
  const key = item.clusterKey || `g:${place.id}`;
  const el = document.createElement("div");
  el.className = `pin${stacked ? " stacked" : ""}`;
  el.style.pointerEvents = "auto";
  el.dataset.key = key;
  el.dataset.count = String(places.length);
  const withPhoto = places.find((p) => p.photos && p.photos[0]) || place;
  const cover = withPhoto.photos && withPhoto.photos[0];
  const head = cover ? `<img src="${cover.thumb}" alt="" />` : `<span>📍</span>`;
  const label = stacked
    ? `${places.length} 段回忆`
    : place.title;
  const badge = stacked ? `<i class="count">${places.length}</i>` : "";
  el.innerHTML = `
    <div class="head" style="border-color:${place.author.color}">${head}${badge}</div>
    <div class="label">${escapeHTML(label)}</div>`;
  el.title = stacked
    ? `${places.length} 段回忆在这里，点开或悬停看看`
    : `${place.title}｜${place.author.display_name}`;

  el.addEventListener("pointerenter", (e) => {
    if (e.pointerType !== "mouse") return;
    clearTimeout(state.bloomLeaveTimer);
    if (stacked) bloomOnPin(el);
  });
  el.addEventListener("pointerleave", (e) => {
    if (e.pointerType !== "mouse") return;
    scheduleCollapseBloom();
  });
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    swallowGlobeClick();
    if (stacked) {
      revealCluster(el, { pin: true });
      return;
    }
    collapseBloom();
    selectPlace(place.id);
  });
  if (state.bloomKey === key && stacked) el.classList.add("blooming");
  return el;
}

function drawPins() {
  globe.htmlElementsData([...state.mapLabels, ...clusterPlaces()]);
}

function nearestCluster(at) {
  const clusters = clusterPlaces();
  if (!clusters.length || !at) return null;
  let best = clusters[0];
  let bestDist = angularDistance(at, best);
  for (const cluster of clusters.slice(1)) {
    const dist = angularDistance(at, cluster);
    if (dist < bestDist) {
      best = cluster;
      bestDist = dist;
    }
  }
  return { cluster: best, dist: bestDist };
}

function clusterHitPx(x, y, maxPx = 72) {
  let best = null;
  let bestDist = maxPx;
  for (const el of document.querySelectorAll(".pin")) {
    const box = el.getBoundingClientRect();
    const cx = box.left + box.width / 2;
    const cy = box.top + box.height / 2;
    const dist = Math.hypot(x - cx, y - cy);
    if (dist < bestDist) {
      bestDist = dist;
      best = el;
    }
  }
  return best;
}

function flyTo(lat, lng, altitude = 1.5) {
  globe.pointOfView({ lat, lng, altitude }, 900);
}

function pauseRotation() {
  controls.autoRotate = false;
}

function altitude() {
  return globe.pointOfView().altitude;
}

function zoomLayerName(alt) {
  if (alt > 1.7) return "国家";
  if (alt > 0.85) return "省 / 州";
  if (alt > 0.4) return "城市";
  return "更细";
}

function nominatimZoom(alt) {
  if (alt > 1.7) return 3;
  if (alt > 0.85) return 5;
  if (alt > 0.4) return 10;
  return 16;
}

function refreshLabels() {
  const level = $("#zoom-level");
  if (level) level.textContent = zoomLayerName(altitude());
  if (state.hover && altitude() <= 1.7) {
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => lookupHoverName(state.hover), 160);
  } else if (state.hover && altitude() > 1.7) {
    showHoverName(state.hoverCountry, state.hover.x, state.hover.y);
  }
}

function showHoverName(name, x, y) {
  const el = $("#hover-name");
  if (!el) return;
  if (!name) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = name;
  if (Number.isFinite(x) && Number.isFinite(y)) {
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  }
}

let labelTimer = 0;
controls.addEventListener("change", () => {
  clearTimeout(labelTimer);
  labelTimer = setTimeout(refreshLabels, 80);
});

function pointerToLatLng(ev) {
  const cam = globe.camera();
  const canvas = globe.renderer().domElement;
  const rect = canvas.getBoundingClientRect();
  const nx = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  const ny = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  const radius = globe.getGlobeRadius();
  const origin = cam.position;
  const dest = origin.clone();
  dest.set(nx, ny, 0.5);
  dest.unproject(cam);
  const dir = dest.sub(origin.clone()).normalize();
  const b = 2 * (origin.x * dir.x + origin.y * dir.y + origin.z * dir.z);
  const c = origin.x ** 2 + origin.y ** 2 + origin.z ** 2 - radius ** 2;
  const disc = b * b - 4 * c;
  if (disc < 0) return null;
  const t = (-b - Math.sqrt(disc)) / 2;
  if (t < 0) return null;
  const px = origin.x + t * dir.x;
  const py = origin.y + t * dir.y;
  const pz = origin.z + t * dir.z;
  if (typeof globe.toGeoCoords === "function") {
    const geo = globe.toGeoCoords({ x: px, y: py, z: pz });
    if (geo && Number.isFinite(geo.lat) && Number.isFinite(geo.lng)) return geo;
  }
  const r = Math.hypot(px, py, pz) || 1;
  // 与 three-globe 的 polar2Cartesian 互逆：theta = 90° - lng
  return {
    lat: 90 - Math.acos(Math.min(1, Math.max(-1, py / r))) * (180 / Math.PI),
    lng: 90 - Math.atan2(pz, px) * (180 / Math.PI),
  };
}

let hoverTimer = 0;
let hoverSeq = 0;
const globeEl = document.getElementById("globe");
globeEl.addEventListener("pointermove", (ev) => {
  const at = pointerToLatLng(ev);
  if (!at) {
    clearTimeout(hoverTimer);
    state.hover = null;
    showHoverName(null);
    return;
  }
  at.x = ev.clientX;
  at.y = ev.clientY;
  const movedFar = state.hover && angularDistance(state.hover, at) > 0.45;
  state.hover = at;
  if (altitude() > 1.7) {
    showHoverName(state.hoverCountry, at.x, at.y);
    return;
  }
  if (movedFar) {
    hoverSeq += 1;
    showHoverName(null);
  } else {
    const current = $("#hover-name");
    if (current && !current.hidden && current.textContent) {
      showHoverName(current.textContent, at.x, at.y);
    }
  }
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => lookupHoverName(at), 220);
});
globeEl.addEventListener("pointerleave", () => {
  clearTimeout(hoverTimer);
  hoverSeq += 1;
  state.hover = null;
  state.hoverCountry = null;
  showHoverName(null);
});

function touchDist(e) {
  const [a, b] = e.touches;
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

function touchMid(e) {
  const [a, b] = e.touches;
  return { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 };
}

const pinch = { dist: 0, opened: false };
globeEl.addEventListener(
  "touchstart",
  (e) => {
    if (e.touches.length === 2) {
      pinch.dist = touchDist(e);
      pinch.opened = false;
    }
  },
  { passive: true }
);
globeEl.addEventListener(
  "touchmove",
  (e) => {
    if (e.touches.length !== 2 || pinch.opened) return;
    const scale = touchDist(e) / (pinch.dist || 1);
    if (scale < 1.12) return;
    const mid = touchMid(e);
    const el = clusterHitPx(mid.x, mid.y, 96);
    if (!el || Number(el.dataset.count) < 2) return;
    pinch.opened = true;
    e.preventDefault();
    revealCluster(el, { pin: true });
  },
  { passive: false }
);

async function lookupHoverName(at) {
  const seq = ++hoverSeq;
  const zoom = nominatimZoom(altitude());
  try {
    const r = await api(`/api/reverse?lat=${at.lat}&lng=${at.lng}&zoom=${zoom}`);
    if (seq !== hoverSeq) return;
    const name = r.title || r.name;
    const pos = state.hover || at;
    showHoverName(name, pos.x, pos.y);
  } catch {
    if (seq !== hoverSeq) return;
    showHoverName(null);
  }
}

function zoomBy(factor) {
  pauseRotation();
  const pov = globe.pointOfView();
  const next = Math.min(3.8, Math.max(0.16, pov.altitude * factor));
  globe.pointOfView({ ...pov, altitude: next }, 280);
  setTimeout(refreshLabels, 300);
}

$("#zoom-in").addEventListener("click", () => zoomBy(0.62));
$("#zoom-out").addEventListener("click", () => zoomBy(1.55));

function angularDistance(a, b) {
  const dLat = a.lat - b.lat;
  const dLng = (a.lng - b.lng + 540) % 360 - 180;
  return Math.hypot(dLat, dLng * Math.cos((a.lat * Math.PI) / 180));
}

function onGlobeClick({ lat, lng }) {
  if (performance.now() < swallowGlobeClickUntil) return;
  pauseRotation();
  if (state.bloomKey) {
    collapseBloom();
    return;
  }
  const found = nearestCluster({ lat, lng });
  if (!found) {
    toast("还没有回忆。点下面的「增加地点」放上第一个");
    return;
  }
  if (found.cluster.places.length > 1) {
    revealCluster(pinElByKey(found.cluster.clusterKey), { pin: true });
    return;
  }
  selectPlace(found.cluster.places[0].id);
}

function openPlacesAt(places) {
  if (!places.length) return;
  if (places.length === 1) {
    collapseBloom();
    selectPlace(places[0].id);
    return;
  }
  const ids = new Set(places.map((p) => p.id));
  const cluster = clusterPlaces().find((c) => c.places.some((p) => ids.has(p.id)));
  revealCluster(pinElByKey(cluster?.clusterKey), { pin: true });
}

function renderClusterModal(places) {
  const named = places.find((p) => p.location_name)?.location_name;
  $("#cluster-title").textContent = named || "这里的回忆";
  $("#cluster-hint").textContent = `这个地点有 ${places.length} 段回忆，点开想看的那一段。`;
  $("#cluster-list").innerHTML = places
    .map((p) => {
      const cover = p.photos[0];
      const thumb = cover
        ? `<img class="thumb" src="${cover.thumb}" alt="" />`
        : `<div class="thumb">📍</div>`;
      return `
        <button class="cluster-item" data-id="${p.id}">
          ${thumb}
          <div class="meta">
            <b>${escapeHTML(p.title)}</b>
            <small>
              <i style="width:7px;height:7px;border-radius:50%;background:${p.author.color};display:inline-block"></i>
              ${escapeHTML(p.author.display_name)}
              ${p.date ? ` · ${fmtDate(p.date)}` : ""}
              ${p.photos.length ? ` · ${p.photos.length} 张` : ""}
            </small>
          </div>
        </button>`;
    })
    .join("");
  $("#modal-cluster").classList.add("open");
}

function closeClusterModal() {
  $("#modal-cluster").classList.remove("open");
}

$("#cluster-close").addEventListener("click", closeClusterModal);
$("#modal-cluster").addEventListener("click", (e) => {
  if (e.target.id === "modal-cluster") closeClusterModal();
});
$("#cluster-list").addEventListener("click", (e) => {
  const btn = e.target.closest(".cluster-item");
  if (!btn) return;
  closeClusterModal();
  selectPlace(Number(btn.dataset.id));
});

async function loadGeo() {
  try {
    const [borders, labels] = await Promise.all([
      fetch(R + "/static/geo/countries.geojson").then((r) => r.json()),
      fetch(R + "/static/geo/labels.json").then((r) => r.json()),
    ]);
    state.geo = labels;
    globe.polygonsData(borders.features).polygonGeoJsonGeometry((d) => d.geometry);
    refreshLabels();
  } catch {
    /* 离线时没有国界也不影响回忆 */
  }
}

// --------------------------------------------------------------------------
// 时间线与详情
// --------------------------------------------------------------------------

function renderTimeline() {
  const list = $("#place-list");
  if (!state.places.length) {
    list.innerHTML = `<div class="empty">还没有回忆。<br />点下面的「增加地点」<br />放上第一段吧。</div>`;
    return;
  }
  list.innerHTML = state.places
    .map((p) => {
      const cover = p.photos[0];
      const thumb = cover
        ? `<img class="thumb" src="${cover.thumb}" alt="" />`
        : `<div class="thumb">📍</div>`;
      return `
        <button class="place-item ${state.selected === p.id ? "on" : ""}" data-id="${p.id}">
          ${thumb}
          <div class="meta">
            <b>${escapeHTML(p.title)}</b>
            <small>
              <i style="width:7px;height:7px;border-radius:50%;background:${p.author.color};display:inline-block"></i>
              ${escapeHTML(p.date ? fmtDate(p.date) : p.author.display_name)}
              ${p.photos.length ? `· ${p.photos.length} 张` : ""}
            </small>
          </div>
        </button>`;
    })
    .join("");
}

$("#place-list").addEventListener("click", (e) => {
  const btn = e.target.closest(".place-item");
  if (btn) selectPlace(Number(btn.dataset.id));
});

function selectPlace(id) {
  const place = state.places.find((p) => p.id === id);
  if (!place) return;
  collapseBloom();
  closeClusterModal();
  state.selected = id;
  pauseRotation();
  flyTo(place.lat, place.lng, 0.55);
  renderTimeline();
  renderDetail(place);
  openDrawer("detail");
}

function renderDetail(place) {
  $("#d-title").textContent = place.title;

  const chips = [];
  if (place.date) chips.push(`📅 ${fmtDate(place.date)}`);
  if (place.location_name) chips.push(`📍 ${place.location_name}`);
  chips.push(`✎ ${place.author.display_name}`);
  chips.push(`${place.lat.toFixed(2)}°, ${place.lng.toFixed(2)}°`);
  $("#d-meta").innerHTML = chips
    .map((c) => `<span class="chip">${escapeHTML(c)}</span>`)
    .join("");

  $("#d-desc").textContent = place.description || "";
  $("#d-desc").style.display = place.description ? "" : "none";

  const mine = place.author.id === state.me.id;
  $("#d-photos").innerHTML =
    place.photos
      .map(
        (ph, i) => `
        <div class="cell">
          <img src="${ph.thumb}" data-index="${i}" alt="" />
          ${mine ? `<button class="del" data-photo="${ph.id}">✕</button>` : ""}
        </div>`
      )
      .join("") + `<button class="add-photo" id="d-add-photo">＋</button>`;

  $("#d-delete").style.display = mine ? "" : "none";
  state.lightbox.photos = place.photos;
}

$("#d-photos").addEventListener("click", async (e) => {
  const img = e.target.closest("img[data-index]");
  if (img) return openLightbox(Number(img.dataset.index));

  const del = e.target.closest("[data-photo]");
  if (del) {
    if (!confirm("删除这张照片？")) return;
    await api(`/api/photos/${del.dataset.photo}`, { method: "DELETE" });
    await reloadPlaces(state.selected);
    toast("照片已删除");
    return;
  }

  if (e.target.closest("#d-add-photo")) photoInput.click();
});

const photoInput = Object.assign(document.createElement("input"), {
  type: "file",
  accept: "image/*",
  multiple: true,
});
photoInput.addEventListener("change", async () => {
  if (!photoInput.files.length || !state.selected) return;
  const fd = new FormData();
  [...photoInput.files].forEach((f) => fd.append("photos", f));
  photoInput.value = "";
  toast("正在上传照片…");
  try {
    await api(`/api/places/${state.selected}/photos`, { method: "POST", body: fd });
    await reloadPlaces(state.selected);
    toast("照片已添加");
  } catch (err) {
    toast(err.message);
  }
});

$("#d-delete").addEventListener("click", async () => {
  const place = state.places.find((p) => p.id === state.selected);
  if (!place || !confirm(`删除「${place.title}」以及它的照片？`)) return;
  await api(`/api/places/${place.id}`, { method: "DELETE" });
  state.selected = null;
  closeDrawers();
  await reloadPlaces();
  toast("已从地球上移除");
});

$("#d-comment").addEventListener("click", () => {
  const place = state.places.find((p) => p.id === state.selected);
  if (!place) return;
  setChatRef(place);
  openDrawer("chat");
});

// --------------------------------------------------------------------------
// 抽屉
// --------------------------------------------------------------------------

function openDrawer(which) {
  $("#drawer-detail").classList.toggle("open", which === "detail");
  $("#drawer-chat").classList.toggle("open", which === "chat");
  if (which === "chat") markRead();
}

function closeDrawers() {
  $("#drawer-detail").classList.remove("open");
  $("#drawer-chat").classList.remove("open");
}

$("#d-close").addEventListener("click", closeDrawers);
$("#c-close").addEventListener("click", closeDrawers);

// --------------------------------------------------------------------------
// 添加地点
// --------------------------------------------------------------------------

const placeModal = $("#modal-place");

function openPlaceModal({ lat, lng }) {
  state.draft = { lat, lng, fromSearch: false, query: "" };
  state.searchHits = [];
  state.files = [];
  $("#f-title").value = "";
  $("#f-date").value = new Date().toISOString().slice(0, 10);
  $("#f-loc").value = "";
  $("#f-desc").value = "";
  $("#f-err").textContent = "";
  $("#f-preview").innerHTML = "";
  $("#f-suggest").hidden = true;
  updateCoordHint();
  globe.ringsData([{ lat, lng }]);
  flyTo(lat, lng, 1.6);
  placeModal.classList.add("open");
  setTimeout(() => $("#f-title").focus(), 220);

  // 反查地名当作默认值，失败就算了
  api(`/api/reverse?lat=${lat}&lng=${lng}`)
    .then((r) => {
      if (r.name && !$("#f-loc").value) $("#f-loc").value = r.name;
    })
    .catch(() => {});
}

function updateCoordHint() {
  const { lat, lng } = state.draft;
  $("#m-coord").textContent = `坐标 ${lat.toFixed(3)}°, ${lng.toFixed(3)}° —— 也可以在下面搜索地名来校准`;
}

function closePlaceModal() {
  placeModal.classList.remove("open");
  globe.ringsData([]);
  state.draft = null;
}

$("#f-cancel").addEventListener("click", closePlaceModal);
placeModal.addEventListener("click", (e) => {
  if (e.target === placeModal) closePlaceModal();
});
$("#btn-add").addEventListener("click", () => {
  const pov = globe.pointOfView();
  openPlaceModal({ lat: pov.lat, lng: pov.lng });
});

// 地名搜索
let searchTimer;
$("#f-loc").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (state.draft) {
    state.draft.fromSearch = false;
    state.draft.query = q;
  }
  if (q.length < 2) {
    $("#f-suggest").hidden = true;
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const results = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      if (!results.length) {
        $("#f-suggest").hidden = true;
        return;
      }
      state.searchHits = results;
      $("#f-suggest").innerHTML = results
        .map(
          (r, i) => `
          <button type="button" data-i="${i}">
            <span class="sg-title">${escapeHTML(r.title || r.name)}${r.kind ? ` · ${escapeHTML(r.kind)}` : ""}</span>
            <span class="sg-path">${escapeHTML(r.path || r.name)}</span>
          </button>`
        )
        .join("");
      $("#f-suggest").hidden = false;
    } catch {
      $("#f-suggest").hidden = true;
    }
  }, 420);
});

$("#f-suggest").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const hit = state.searchHits[Number(btn.dataset.i)];
  if (!hit) return;
  state.draft = { lat: hit.lat, lng: hit.lng, fromSearch: true, query: hit.name };
  $("#f-loc").value = hit.name;
  $("#f-suggest").hidden = true;
  updateCoordHint();
  globe.ringsData([{ lat: hit.lat, lng: hit.lng }]);
  flyTo(hit.lat, hit.lng, 0.32);
});

// 图片选择与拖拽
$("#f-drop").addEventListener("click", () => $("#f-files").click());
$("#f-files").addEventListener("change", (e) => addFiles(e.target.files));
["dragover", "dragleave", "drop"].forEach((type) => {
  $("#f-drop").addEventListener(type, (e) => {
    e.preventDefault();
    $("#f-drop").classList.toggle("over", type === "dragover");
    if (type === "drop") addFiles(e.dataTransfer.files);
  });
});

function addFiles(fileList) {
  for (const file of fileList) {
    if (file.type.startsWith("image/")) state.files.push(file);
  }
  renderPreview();
}

function renderPreview() {
  $("#f-preview").innerHTML = state.files
    .map(
      (f, i) =>
        `<div class="p"><img src="${URL.createObjectURL(f)}" alt="" /><button data-i="${i}">✕</button></div>`
    )
    .join("");
}

$("#f-preview").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  state.files.splice(Number(btn.dataset.i), 1);
  renderPreview();
});

$("#f-save").addEventListener("click", async () => {
  const title = $("#f-title").value.trim();
  if (!title) {
    $("#f-err").textContent = "给这个地方起个名字吧";
    return;
  }
  const loc = $("#f-loc").value.trim();
  $("#f-save").disabled = true;
  $("#f-save").textContent = "保存中…";
  $("#f-err").textContent = "";
  try {
    if (loc && !(state.draft && state.draft.fromSearch && state.draft.query === loc)) {
      const results = await api(`/api/geocode?q=${encodeURIComponent(loc)}`);
      if (!results.length) {
        throw new Error("没找到这个地点。请从下拉列表里选一个，或写得更完整，比如「沙市区 湖北」");
      }
      const hit = results[0];
      state.draft = { lat: hit.lat, lng: hit.lng, fromSearch: true, query: hit.name };
      $("#f-loc").value = hit.name;
      updateCoordHint();
    }
    const fd = new FormData();
    fd.append("title", title);
    fd.append("lat", state.draft.lat);
    fd.append("lng", state.draft.lng);
    fd.append("place_date", $("#f-date").value);
    fd.append("location_name", $("#f-loc").value.trim());
    fd.append("description", $("#f-desc").value.trim());
    state.files.forEach((f) => fd.append("photos", f));

    const place = await api("/api/places", { method: "POST", body: fd });
    closePlaceModal();
    await reloadPlaces(place.id);
    toast("已经放到地球上了");
  } catch (err) {
    $("#f-err").textContent = err.message;
  } finally {
    $("#f-save").disabled = false;
    $("#f-save").textContent = "保存到地球";
  }
});

// --------------------------------------------------------------------------
// 留言
// --------------------------------------------------------------------------

function setChatRef(place) {
  state.chatRef = place;
  $("#chat-ref").classList.toggle("on", Boolean(place));
  if (place) $("#chat-ref-text").textContent = `关于「${place.title}」`;
}

$("#chat-ref-clear").addEventListener("click", () => setChatRef(null));

function renderMessages(messages, append = true) {
  const list = $("#chat-list");
  if (!append) list.innerHTML = "";
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 120;

  for (const m of messages) {
    const place = state.places.find((p) => p.id === m.place_id);
    const el = document.createElement("div");
    el.className = `msg ${m.mine ? "mine" : ""}`;
    el.innerHTML = `
      <div class="avatar" style="background:${m.author.color}">${initials(m.author.display_name)}</div>
      <div>
        ${place ? `<div class="ref" data-place="${place.id}">📍 ${escapeHTML(place.title)}</div>` : ""}
        <div class="bubble">${escapeHTML(m.body)}</div>
        <time>${fmtTime(m.created_at)}</time>
      </div>`;
    list.appendChild(el);
    state.lastMsgId = Math.max(state.lastMsgId, m.id);
  }

  if (!messages.length) return;
  if (nearBottom || !append) list.scrollTop = list.scrollHeight;
}

$("#chat-list").addEventListener("click", (e) => {
  const ref = e.target.closest("[data-place]");
  if (ref) selectPlace(Number(ref.dataset.place));
});

async function pollMessages() {
  try {
    const data = await api(`/api/messages?after=${state.lastMsgId}`);
    if (data.messages.length) {
      renderMessages(data.messages);
      const chatOpen = $("#drawer-chat").classList.contains("open");
      if (chatOpen) markRead();
    }
    updateUnread(data.unread);
  } catch {
    /* 网络抖动就等下一轮 */
  }
}

function updateUnread(count) {
  state.unread = count;
  const badge = $("#unread");
  badge.hidden = !count;
  badge.textContent = count > 99 ? "99+" : count;
}

async function markRead() {
  if (!state.lastMsgId) return;
  try {
    await apiJSON("/api/messages/read", "POST", { last_id: state.lastMsgId });
    updateUnread(0);
  } catch {
    /* 忽略 */
  }
}

async function sendMessage() {
  const input = $("#chat-text");
  const body = input.value.trim();
  if (!body) return;
  input.value = "";
  input.style.height = "auto";
  try {
    const msg = await apiJSON("/api/messages", "POST", {
      body,
      place_id: state.chatRef ? state.chatRef.id : null,
    });
    renderMessages([msg]);
    setChatRef(null);
    markRead();
  } catch (err) {
    toast(err.message);
    input.value = body;
  }
}

$("#chat-send").addEventListener("click", sendMessage);
$("#chat-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    sendMessage();
  }
});
$("#chat-text").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
});

$("#btn-chat").addEventListener("click", () => {
  const open = $("#drawer-chat").classList.contains("open");
  if (open) closeDrawers();
  else {
    openDrawer("chat");
    $("#chat-list").scrollTop = $("#chat-list").scrollHeight;
  }
});

// --------------------------------------------------------------------------
// 邀请
// --------------------------------------------------------------------------

const inviteModal = $("#modal-invite");

$("#btn-invite").addEventListener("click", async () => {
  inviteModal.classList.add("open");
  await refreshInvites();
});
$("#i-close").addEventListener("click", () => inviteModal.classList.remove("open"));
inviteModal.addEventListener("click", (e) => {
  if (e.target === inviteModal) inviteModal.classList.remove("open");
});

async function refreshInvites() {
  let list = await api("/api/invites");
  // 打开这个窗口就是想邀请人，没有可用的码就直接备一个
  if (!list.some((i) => !i.used_at)) {
    await apiJSON("/api/invites", "POST", {});
    list = await api("/api/invites");
  }
  const unused = list.find((i) => !i.used_at);
  $("#invite-code").textContent = unused ? unused.code : "还没有邀请码";

  const { public_url: publicUrl } = await api("/api/config").catch(() => ({}));
  state.shareUrl = publicUrl || location.origin;
  const local = /^https?:\/\/(localhost|127\.0\.0\.1|10\.|192\.168\.|172\.)/.test(state.shareUrl);
  $("#invite-list").innerHTML = `
    <div style="margin-bottom:10px">把这个地址和邀请码一起发给对方：<b>${escapeHTML(state.shareUrl)}</b></div>
    ${
      local
        ? `<div style="color:#ffb703;margin-bottom:10px">这是内网地址，只有同一个 Wi-Fi 下能打开。要发给外网的人，先运行 ./share.sh 拿公网地址。</div>`
        : ""
    }
    ${list
      .map(
        (i) =>
          `<div>${i.code} —— ${i.used_at ? `已被 ${escapeHTML(i.used_by_name)} 使用` : "等待使用"}</div>`
      )
      .join("")}`;
}

$("#i-new").addEventListener("click", async () => {
  const { code } = await apiJSON("/api/invites", "POST", {});
  $("#invite-code").textContent = code;
  await refreshInvites();
  toast("新的邀请码已生成");
});

$("#i-copy").addEventListener("click", async () => {
  const code = $("#invite-code").textContent.trim();
  const text = `来我们的地球上放点回忆吧：${state.shareUrl || location.origin}\n邀请码：${code}`;
  try {
    await navigator.clipboard.writeText(text);
    toast("邀请信息已复制");
  } catch {
    toast(text);
  }
});

// --------------------------------------------------------------------------
// 灯箱
// --------------------------------------------------------------------------

function openLightbox(index) {
  state.lightbox.index = index;
  $("#lb-img").src = state.lightbox.photos[index].url;
  $("#lightbox").classList.add("open");
}

function stepLightbox(delta) {
  const { photos } = state.lightbox;
  if (!photos.length) return;
  state.lightbox.index = (state.lightbox.index + delta + photos.length) % photos.length;
  $("#lb-img").src = photos[state.lightbox.index].url;
}

$("#lb-close").addEventListener("click", () => $("#lightbox").classList.remove("open"));
$("#lb-prev").addEventListener("click", () => stepLightbox(-1));
$("#lb-next").addEventListener("click", () => stepLightbox(1));
$("#lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") $("#lightbox").classList.remove("open");
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if ($("#lightbox").classList.contains("open")) $("#lightbox").classList.remove("open");
    else if (state.bloomKey) collapseBloom();
    else if ($("#modal-cluster").classList.contains("open")) closeClusterModal();
    else if (placeModal.classList.contains("open")) closePlaceModal();
    else if (inviteModal.classList.contains("open")) inviteModal.classList.remove("open");
    else closeDrawers();
  }
  if ($("#lightbox").classList.contains("open")) {
    if (e.key === "ArrowLeft") stepLightbox(-1);
    if (e.key === "ArrowRight") stepLightbox(1);
  }
});

// --------------------------------------------------------------------------
// 启动
// --------------------------------------------------------------------------

$("#btn-logout").addEventListener("click", () => {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = R + "/api/auth/logout";
  document.body.appendChild(form);
  form.submit();
});

function renderMembers() {
  $("#members").innerHTML = state.members
    .map(
      (m) =>
        `<div class="avatar" style="background:${m.color}" title="${escapeHTML(m.display_name)}">${initials(
          m.display_name
        )}</div>`
    )
    .join("");
  $("#brand-sub").textContent =
    state.members.length > 1
      ? `${state.members.map((m) => m.display_name).join(" & ") } 的共同回忆`
      : "邀请一个人，一起收藏去过的地方";
}

async function reloadPlaces(selectId = null) {
  state.places = await api("/api/places");
  drawPins();
  renderTimeline();
  if (selectId) selectPlace(selectId);
  else if (state.selected) {
    const place = state.places.find((p) => p.id === state.selected);
    if (place) renderDetail(place);
  }
}

async function boot() {
  if (R === "/demo") {
    document.body.classList.add("is-demo");
    const bar = $("#demo-banner");
    if (bar) bar.hidden = false;
  }
  globe.width(window.innerWidth).height(window.innerHeight);
  try {
    state.me = await api("/api/auth/me");
  } catch {
    return;
  }
  state.members = await api("/api/members");
  renderMembers();
  await Promise.all([reloadPlaces(), loadGeo()]);

  const data = await api("/api/messages");
  renderMessages(data.messages, false);
  updateUnread(data.unread);

  setInterval(pollMessages, 4000);

  if (state.places.length) {
    const last = state.places[state.places.length - 1];
    flyTo(last.lat, last.lng, 2.2);
  }
  refreshLabels();
  setTimeout(() => $("#loading").classList.add("gone"), 600);
}

boot();
