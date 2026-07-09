import { useEffect, useRef } from "react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export interface SubTab {
  value: string;
  label: string;
}

interface SubTabBarProps {
  tabs: SubTab[];
  value: string;
  onValueChange: (value: string) => void;
}

export function SubTabBar({ tabs, value, onValueChange }: SubTabBarProps) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>(
      '[data-state="active"]',
    );
    active?.scrollIntoView({
      inline: "nearest",
      block: "nearest",
      behavior: "smooth",
    });
  }, [value]);

  return (
    <div className="no-select min-w-0 shrink-0 border-b">
      <Tabs value={value} onValueChange={onValueChange}>
        <div className="flex h-10 items-center overflow-x-auto px-6">
          <TabsList
            ref={listRef}
            className="h-8 w-max justify-start bg-transparent p-0 gap-1"
          >
            {tabs.map((tab) => (
              <TabsTrigger
                key={tab.value}
                value={tab.value}
                className="h-7 shrink-0 rounded-md px-3 text-sm font-medium data-[state=active]:bg-accent data-[state=active]:text-accent-foreground data-[state=inactive]:text-muted-foreground"
              >
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>
    </div>
  );
}
