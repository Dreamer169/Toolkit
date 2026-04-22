import { useBrowserStore } from "@/hooks/use-browser-store";
import { Plus, X } from "lucide-react";
import { DragDropContext, Droppable, Draggable, DropResult } from "@hello-pangea/dnd";
import { cn } from "@/lib/utils";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { motion, AnimatePresence } from "framer-motion";

export function TabBar() {
  const { tabs, activeTabId, addTab, closeTab, switchTab, reorderTabs } = useBrowserStore();

  const handleDragEnd = (result: DropResult) => {
    if (!result.destination) return;
    
    const items = Array.from(tabs);
    const [reorderedItem] = items.splice(result.source.index, 1);
    items.splice(result.destination.index, 0, reorderedItem);
    
    reorderTabs(items);
  };

  return (
    <div className="flex items-end bg-background/50 backdrop-blur border-b border-border pt-2 px-2 gap-2">
      <DragDropContext onDragEnd={handleDragEnd}>
        <Droppable droppableId="tabs" direction="horizontal">
          {(provided) => (
            <ScrollArea className="flex-1 w-full whitespace-nowrap" type="scroll">
              <div
                ref={provided.innerRef}
                {...provided.droppableProps}
                className="flex w-max items-end gap-1 h-9 px-1"
              >
                <AnimatePresence initial={false}>
                  {tabs.map((tab, index) => (
                    <Draggable key={tab.id} draggableId={tab.id} index={index}>
                      {(provided, snapshot) => (
                        <motion.div
                          ref={provided.innerRef}
                          {...provided.draggableProps}
                          {...provided.dragHandleProps}
                          initial={{ opacity: 0, y: 10, scale: 0.95 }}
                          animate={{ opacity: 1, y: 0, scale: 1 }}
                          exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.2 } }}
                          transition={{ duration: 0.2 }}
                          className={cn(
                            "group relative flex items-center h-8 min-w-[120px] max-w-[240px] px-3 border-x border-t rounded-t-lg transition-colors cursor-default select-none",
                            activeTabId === tab.id
                              ? "bg-background border-border z-10"
                              : "bg-muted/50 border-transparent hover:bg-muted text-muted-foreground z-0",
                            snapshot.isDragging && "z-50 shadow-xl opacity-90 ring-1 ring-primary/20"
                          )}
                          onMouseDown={(e) => {
                            if (e.button === 0) switchTab(tab.id); // Left click
                            if (e.button === 1) { // Middle click
                              e.preventDefault();
                              closeTab(tab.id);
                            }
                          }}
                        >
                          <div className="flex items-center gap-2 overflow-hidden flex-1">
                            {tab.isLoading ? (
                              <div className="h-4 w-4 rounded-full border-2 border-primary border-t-transparent animate-spin flex-shrink-0" />
                            ) : tab.favicon ? (
                              <img src={tab.favicon} alt="" className="h-4 w-4 flex-shrink-0 rounded-sm bg-white" onError={(e) => (e.currentTarget.style.display = 'none')} />
                            ) : (
                              <div className="h-4 w-4 rounded-sm bg-muted flex-shrink-0" />
                            )}
                            <span className="text-xs truncate flex-1">{tab.title}</span>
                          </div>
                          
                          <button
                            className={cn(
                              "ml-2 h-5 w-5 flex-shrink-0 rounded-full flex items-center justify-center transition-opacity hover:bg-muted-foreground/20",
                              activeTabId === tab.id ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                            )}
                            onClick={(e) => {
                              e.stopPropagation();
                              closeTab(tab.id);
                            }}
                          >
                            <X className="h-3 w-3" />
                          </button>
                          
                          {/* Separator line between tabs when inactive */}
                          {activeTabId !== tab.id && index < tabs.length - 1 && tabs[index + 1]?.id !== activeTabId && (
                            <div className="absolute right-0 top-2 bottom-2 w-px bg-border translate-x-[0.5px]" />
                          )}
                        </motion.div>
                      )}
                    </Draggable>
                  ))}
                </AnimatePresence>
                {provided.placeholder}
              </div>
              <ScrollBar orientation="horizontal" className="hidden" />
            </ScrollArea>
          )}
        </Droppable>
      </DragDropContext>
      
      <button
        onClick={() => addTab()}
        className="h-8 w-8 rounded-full flex items-center justify-center hover:bg-muted transition-colors flex-shrink-0 mb-0.5 text-muted-foreground hover:text-foreground"
      >
        <Plus className="h-5 w-5" />
      </button>
    </div>
  );
}
