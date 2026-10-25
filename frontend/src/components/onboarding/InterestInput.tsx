"use client";

interface InterestInputProps {
  value: string;
  onChange: (v: string) => void;
}

export function InterestInput({ value, onChange }: InterestInputProps) {
  return (
    <div>
      <label htmlFor="interest-text" className="mb-2 block font-semibold">
        Describe what you read and care about
      </label>
      <textarea
        id="interest-text"
        className="input resize-y text-[0.95rem] leading-relaxed"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={5}
        placeholder="I work in ML infrastructure, care about distributed training, follow the Kubernetes ecosystem closely, and read about the economics of AI."
      />
      <p className="mt-1.5 text-xs text-[var(--text-tertiary)]">
        Be specific. The more detail you provide, the better your first digest will be.
      </p>
      <p className="mt-3 text-xs text-[var(--text-tertiary)]">
        Weirling learns from what you read. See{" "}
        <a href="/privacy" target="_blank" rel="noopener" className="underline hover:text-[var(--text)]">
          exactly what it records and why
        </a>
        .
      </p>
    </div>
  );
}
