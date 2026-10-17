import { useId, type ReactNode } from "react";

// Both admin navigation and packet navigation use the same keyboard behavior.
export function Tabs({ tabs, selected, onSelect, label, children }: {
  tabs: { id: string; label: string }[];
  selected: string;
  onSelect: (id: string) => void;
  label: string;
  children: ReactNode;
}) {
  const id = useId();
  return <>
    <div role="tablist" aria-label={label} className="ui-tabs">
      {tabs.map((tab, index) => <button key={tab.id} id={`${id}-${tab.id}`} role="tab"
        aria-selected={selected === tab.id} aria-controls={`${id}-panel`}
        tabIndex={selected === tab.id ? 0 : -1}
        onClick={() => onSelect(tab.id)}
        onKeyDown={(event) => {
          const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
            : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
            : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
          if (next < 0) return;
          event.preventDefault();
          onSelect(tabs[next].id);
          const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
          buttons?.[next].focus();
          buttons?.[next].scrollIntoView?.({ block: "nearest", inline: "nearest" });
        }}>{tab.label}</button>)}
    </div>
    <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${selected}`}>{children}</div>
  </>;
}
