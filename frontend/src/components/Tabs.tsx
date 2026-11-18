import type { ReactNode } from "react";
import { Tabs as TabsPrimitive } from "radix-ui";

// Admin navigation and packet navigation share it. Radix gives the tab pattern (arrow keys,
// Home/End, roles and ids); the look is the app's underline tabs.
export function Tabs({ tabs, selected, onSelect, label, children }: {
  tabs: { id: string; label: string }[];
  selected: string;
  onSelect: (id: string) => void;
  label: string;
  children: ReactNode;
}) {
  return (
    <TabsPrimitive.Root value={selected} onValueChange={onSelect}>
      <TabsPrimitive.List aria-label={label} className="ui-tabs">
        {tabs.map((tab) => <TabsPrimitive.Trigger key={tab.id} value={tab.id}
          // Radix focuses the tab by script on a mouse press, which Chrome shows as keyboard focus.
          onMouseDown={(event) => { event.currentTarget.dataset.pointer = ""; }}
          onBlur={(event) => { delete event.currentTarget.dataset.pointer; }}>{tab.label}</TabsPrimitive.Trigger>)}
      </TabsPrimitive.List>
      <TabsPrimitive.Content value={selected}>{children}</TabsPrimitive.Content>
    </TabsPrimitive.Root>
  );
}
