import { useState } from "react";

import { createPayout, invalidateDashboardCache } from "./lib/api.js";
import { useDashboard } from "./hooks/useDashboard.js";
import { PayoutForm } from "./components/PayoutForm.jsx";

function formatMoney(amountPaise) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format((amountPaise ?? 0) / 100);
}

function classForStatus(status) {
  if (status === "completed") return "bg-mint/20 text-ink";
  if (status === "failed") return "bg-flame/20 text-ink";
  if (status === "processing") return "bg-gold/25 text-ink";
  return "bg-ink/10 text-ink";
}

export default function App() {
  const { dashboard, error: dashboardError, refreshDashboard } = useDashboard();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [notice, setNotice] = useState("");
  const error = submitError || dashboardError;

  async function handleSubmit(payload) {
    setIsSubmitting(true);
    setSubmitError("");
    setNotice("");

    try {
      const createdPayout = await createPayout(payload);
      setNotice(`Payout #${createdPayout.id} created in ${createdPayout.status} state.`);
      invalidateDashboardCache();
      await refreshDashboard({ force: true });
      return true;
    } catch (submitError) {
      setSubmitError(submitError.message);
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(242,95,92,0.18),_transparent_38%),linear-gradient(135deg,_#f7f3e9,_#fffdf8_48%,_#d7efe8)] px-4 py-8 text-ink">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <header className="overflow-hidden rounded-[2rem] border border-white/60 bg-white/70 p-8 shadow-panel backdrop-blur">
          <p className="font-body text-sm uppercase tracking-[0.35em] text-ink/60">Playto Pay</p>
          <div className="mt-4 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <h1 className="font-display text-4xl leading-tight md:text-6xl">Payout engine simulation dashboard</h1>
              <p className="mt-3 max-w-2xl font-body text-base text-ink/70">
                Monitor ledger-backed balances, submit new payouts, and watch asynchronous processing converge toward
                terminal states every 5 seconds.
              </p>
            </div>
            <div className="rounded-2xl bg-ink px-5 py-4 text-foam">
              <p className="font-body text-xs uppercase tracking-[0.25em] text-foam/70">Merchant</p>
              <p className="mt-2 font-display text-2xl">{dashboard?.merchant?.name ?? "Loading..."}</p>
            </div>
          </div>
        </header>

        {error ? <div className="rounded-2xl border border-flame/40 bg-flame/10 px-4 py-3 font-body">{error}</div> : null}
        {notice ? <div className="rounded-2xl border border-mint/40 bg-mint/10 px-4 py-3 font-body">{notice}</div> : null}

        <section className="grid gap-4 md:grid-cols-2">
          <article className="rounded-[1.75rem] bg-ink p-6 text-foam shadow-panel">
            <p className="font-body text-xs uppercase tracking-[0.25em] text-foam/60">Available balance</p>
            <p className="mt-4 font-display text-4xl">{formatMoney(dashboard?.available_balance)}</p>
          </article>
          <article className="rounded-[1.75rem] bg-white/80 p-6 shadow-panel backdrop-blur">
            <p className="font-body text-xs uppercase tracking-[0.25em] text-ink/50">Held balance</p>
            <p className="mt-4 font-display text-4xl">{formatMoney(dashboard?.held_balance)}</p>
          </article>
        </section>

        <section className="grid gap-6 lg:grid-cols-[1.2fr,1fr]">
          <article className="rounded-[1.75rem] bg-white/80 p-6 shadow-panel backdrop-blur">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <h2 className="font-display text-3xl">Recent ledger</h2>
                <p className="font-body text-sm text-ink/60">Every balance movement remains append-only.</p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full font-body text-sm">
                <thead className="text-left text-ink/55">
                  <tr>
                    <th className="pb-3 pr-4">Type</th>
                    <th className="pb-3 pr-4">Amount</th>
                    <th className="pb-3 pr-4">Reference</th>
                    <th className="pb-3">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {(dashboard?.recent_ledger_entries ?? []).map((entry) => (
                    <tr key={entry.id} className="border-t border-ink/10">
                      <td className="py-3 pr-4 capitalize">{entry.entry_type}</td>
                      <td className="py-3 pr-4">{formatMoney(entry.amount_paise)}</td>
                      <td className="py-3 pr-4">
                        {entry.reference_type}:{entry.reference_id}
                      </td>
                      <td className="py-3">{new Date(entry.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <PayoutForm isSubmitting={isSubmitting} onSubmit={handleSubmit} />
        </section>

        <section className="rounded-[1.75rem] bg-white/80 p-6 shadow-panel backdrop-blur">
          <div className="mb-5">
            <h2 className="font-display text-3xl">Payout history</h2>
            <p className="font-body text-sm text-ink/60">Pending requests, in-flight retries, and terminal outcomes.</p>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full font-body text-sm">
              <thead className="text-left text-ink/55">
                <tr>
                  <th className="pb-3 pr-4">ID</th>
                  <th className="pb-3 pr-4">Amount</th>
                  <th className="pb-3 pr-4">Bank</th>
                  <th className="pb-3 pr-4">Status</th>
                  <th className="pb-3 pr-4">Retries</th>
                  <th className="pb-3">Updated</th>
                </tr>
              </thead>
              <tbody>
                {(dashboard?.payout_history ?? []).map((payout) => (
                  <tr key={payout.id} className="border-t border-ink/10">
                    <td className="py-3 pr-4">#{payout.id}</td>
                    <td className="py-3 pr-4">{formatMoney(payout.amount_paise)}</td>
                    <td className="py-3 pr-4">{payout.bank_account_id}</td>
                    <td className="py-3 pr-4">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${classForStatus(payout.status)}`}>
                        {payout.status}
                      </span>
                    </td>
                    <td className="py-3 pr-4">{payout.retry_count}</td>
                    <td className="py-3">{new Date(payout.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}
