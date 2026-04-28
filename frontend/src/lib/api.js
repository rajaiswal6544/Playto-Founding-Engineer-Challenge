export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

const DASHBOARD_CACHE_TTL_MS = 1000;

const dashboardStore = {
  data: null,
  fetchedAt: 0,
  inflight: null,
  latestWriteSequence: 0,
  nextSequence: 0,
};

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  const payload = await response.json();

  if (!response.ok) {
    const message =
      payload && typeof payload === "object"
        ? Object.values(payload).flat().join(" ") || "Request failed."
        : "Request failed.";
    throw new Error(message);
  }

  return payload;
}

function startDashboardRequest() {
  const sequence = ++dashboardStore.nextSequence;
  let request;

  request = fetchJson("/dashboard")
    .then((payload) => {
      if (sequence >= dashboardStore.latestWriteSequence) {
        dashboardStore.data = payload;
        dashboardStore.fetchedAt = Date.now();
        dashboardStore.latestWriteSequence = sequence;
      }
      return payload;
    })
    .finally(() => {
      if (dashboardStore.inflight === request) {
        dashboardStore.inflight = null;
      }
    });

  dashboardStore.inflight = request;
  return request;
}

export function fetchDashboard({ force = false } = {}) {
  const cacheIsFresh = Date.now() - dashboardStore.fetchedAt < DASHBOARD_CACHE_TTL_MS;

  if (!force) {
    if (cacheIsFresh && dashboardStore.data) {
      return Promise.resolve(dashboardStore.data);
    }
    if (dashboardStore.inflight) {
      return dashboardStore.inflight;
    }
  }

  return startDashboardRequest();
}

export function invalidateDashboardCache() {
  dashboardStore.fetchedAt = 0;
}

export function createPayout(input) {
  return fetchJson("/payouts", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify(input),
  });
}

