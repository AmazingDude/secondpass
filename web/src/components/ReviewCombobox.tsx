import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Check, ChevronDown, Search } from "lucide-react";

export type ReviewComboboxOption = {
  id: number;
  workerName: string;
  filePath: string;
  fileName: string;
  createdAt: string;
};

type Props = {
  options: ReviewComboboxOption[];
  value: number | "";
  onChange: (id: number | "") => void;
  disabled?: boolean;
  label: string;
  emptyLabel?: string;
};

function fileName(path: string) {
  return path.replaceAll("\\", "/").split("/").pop() || path;
}

export function ReviewCombobox({
  options,
  value,
  onChange,
  disabled = false,
  label,
  emptyLabel = "No reviews available",
}: Props) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const selected = useMemo(
    () => options.find((option) => option.id === value) ?? null,
    [options, value],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) =>
      `${option.id} ${option.workerName} ${option.filePath} ${option.fileName}`
        .toLowerCase()
        .includes(needle),
    );
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const selectedIndex = filtered.findIndex((option) => option.id === value);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [filtered, open, value]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function selectOption(id: number) {
    onChange(id);
    setOpen(false);
    setQuery("");
  }

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onListKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!filtered.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % filtered.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => (current - 1 + filtered.length) % filtered.length);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const option = filtered[activeIndex];
      if (option) selectOption(option.id);
    }
  }

  return (
    <div className="review-combobox" ref={rootRef}>
      <span className="field-label" id={`${listId}-label`}>
        {label}
      </span>
      <button
        type="button"
        className={`review-combobox-trigger${open ? " is-open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={`${listId}-label`}
        disabled={disabled || options.length === 0}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={onTriggerKeyDown}
      >
        {selected ? (
          <span className="review-combobox-trigger-main">
            <span className="review-combobox-id">#{selected.id}</span>
            <span
              className={`review-combobox-worker review-combobox-worker--${selected.workerName}`}
            >
              {selected.workerName}
            </span>
            <span className="review-combobox-file mono">{selected.fileName}</span>
          </span>
        ) : (
          <span className="review-combobox-placeholder">
            {options.length ? "Select a review" : emptyLabel}
          </span>
        )}
        <ChevronDown className="review-combobox-chevron" aria-hidden />
      </button>

      {open ? (
        <div
          className="review-combobox-panel"
          role="presentation"
          onKeyDown={onListKeyDown}
        >
          <div className="review-combobox-search">
            <Search aria-hidden />
            <input
              ref={searchRef}
              className="field-input"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by ID, worker, or path"
              aria-label="Filter reviews"
            />
          </div>
          <ul
            id={listId}
            className="review-combobox-list"
            role="listbox"
            aria-label={label}
          >
            {filtered.length === 0 ? (
              <li className="review-combobox-empty">No matching reviews</li>
            ) : (
              filtered.map((option, index) => {
                const active = index === activeIndex;
                const isSelected = option.id === value;
                return (
                  <li key={option.id} role="presentation">
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      className={`review-combobox-option${active ? " is-active" : ""}${
                        isSelected ? " is-selected" : ""
                      }`}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => selectOption(option.id)}
                    >
                      <span className="review-combobox-option-top">
                        <span className="review-combobox-id">#{option.id}</span>
                        <span
                          className={`review-combobox-worker review-combobox-worker--${option.workerName}`}
                        >
                          {option.workerName}
                        </span>
                        {isSelected ? (
                          <Check className="review-combobox-check" aria-hidden />
                        ) : null}
                      </span>
                      <span className="review-combobox-option-file mono">
                        {fileName(option.filePath)}
                      </span>
                      <span className="review-combobox-option-meta">
                        {new Date(option.createdAt).toLocaleString()}
                      </span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
