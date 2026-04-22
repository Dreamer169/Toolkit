import { motion, AnimatePresence } from "framer-motion";
import { Keyboard } from "lucide-react";

interface KeyboardShortcutsHelpProps {
  open: boolean;
  onClose: () => void;
}

export function KeyboardShortcutsHelp({ open, onClose }: KeyboardShortcutsHelpProps) {
  const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
  const mod = isMac ? "⌘" : "Ctrl";

  const shortcuts = [
    { keys: [mod, "T"], desc: "New tab" },
    { keys: [mod, "W"], desc: "Close tab" },
    { keys: [mod, "L"], desc: "Focus address bar" },
    { keys: [mod, "R"], desc: "Reload page" },
    { keys: [mod, "Tab"], desc: "Next tab" },
    { keys: [mod, "Shift", "Tab"], desc: "Previous tab" },
    { keys: [mod, "1-9"], desc: "Jump to tab" },
    { keys: [mod, "/"], desc: "Toggle this menu" },
  ];

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-background/80 backdrop-blur-sm z-[100]"
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-card border border-border shadow-2xl rounded-2xl w-full max-w-md p-6 z-[101]"
          >
            <div className="flex items-center gap-3 mb-6">
              <div className="p-2 bg-primary/10 text-primary rounded-lg">
                <Keyboard className="h-5 w-5" />
              </div>
              <h2 className="text-lg font-semibold">Keyboard Shortcuts</h2>
            </div>
            
            <div className="space-y-4">
              {shortcuts.map((s, i) => (
                <div key={i} className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">{s.desc}</span>
                  <div className="flex items-center gap-1">
                    {s.keys.map((key, j) => (
                      <kbd key={j} className="h-6 px-2 rounded bg-muted border border-border border-b-2 text-xs font-mono font-medium flex items-center justify-center text-foreground">
                        {key}
                      </kbd>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            
            <div className="mt-8 text-center">
              <button 
                onClick={onClose}
                className="text-sm text-primary font-medium hover:underline"
              >
                Close
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
