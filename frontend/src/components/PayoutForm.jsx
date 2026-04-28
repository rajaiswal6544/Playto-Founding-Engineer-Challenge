import { useState } from "react";


export function PayoutForm({ isSubmitting, onSubmit }) {
  const [form, setForm] = useState({ amount_paise: "", bank_account_id: "" });

  async function handleSubmit(event) {
    event.preventDefault();
    const payload = {
      amount_paise: Number(form.amount_paise),
      bank_account_id: form.bank_account_id,
    };

    const created = await onSubmit(payload);
    if (created) {
      setForm({ amount_paise: "", bank_account_id: "" });
    }
  }

  return (
    <article className="rounded-[1.75rem] bg-white/80 p-6 shadow-panel backdrop-blur">
      <h2 className="font-display text-3xl">Create payout</h2>
      <p className="mt-2 font-body text-sm text-ink/60">Funds are held immediately, then a worker simulates bank processing.</p>
      <form className="mt-6 flex flex-col gap-4" onSubmit={handleSubmit}>
        <label className="font-body text-sm">
          Amount in paise
          <input
            className="mt-2 w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none ring-0 transition focus:border-ink"
            type="number"
            min="1"
            required
            value={form.amount_paise}
            onChange={(event) => setForm((current) => ({ ...current, amount_paise: event.target.value }))}
          />
        </label>
        <label className="font-body text-sm">
          Bank account ID
          <input
            className="mt-2 w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 outline-none ring-0 transition focus:border-ink"
            type="text"
            required
            value={form.bank_account_id}
            onChange={(event) => setForm((current) => ({ ...current, bank_account_id: event.target.value }))}
          />
        </label>
        <button
          className="rounded-2xl bg-flame px-4 py-3 font-body text-sm font-semibold text-white transition hover:bg-[#de514f] disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isSubmitting}
          type="submit"
        >
          {isSubmitting ? "Submitting..." : "Create payout"}
        </button>
      </form>
    </article>
  );
}

