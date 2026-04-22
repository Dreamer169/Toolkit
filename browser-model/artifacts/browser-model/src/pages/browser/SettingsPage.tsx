import { useBrowserStore } from "@/hooks/use-browser-store";
import { Settings, Shield, Palette, Search, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { useEffect } from "react";

export function SettingsPage() {
  const { settings, updateSettings, clearAllData } = useBrowserStore();

  useEffect(() => {
    const root = window.document.documentElement;
    root.classList.remove("light", "dark");
    if (settings.theme === "system") {
      const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      root.classList.add(systemTheme);
    } else {
      root.classList.add(settings.theme);
    }
  }, [settings.theme]);

  return (
    <div className="max-w-3xl mx-auto py-8 px-6">
      <div className="flex items-center gap-3 mb-8">
        <div className="p-2 bg-primary/10 text-primary rounded-lg">
          <Settings className="h-6 w-6" />
        </div>
        <h1 className="text-2xl font-bold">Settings</h1>
      </div>

      <div className="space-y-8">
        {/* Search Engine */}
        <section className="bg-card border border-border rounded-xl p-6 shadow-sm">
          <div className="flex items-start gap-4 mb-6">
            <Search className="h-5 w-5 text-muted-foreground mt-0.5" />
            <div>
              <h2 className="text-lg font-semibold">Search engine</h2>
              <p className="text-sm text-muted-foreground">Choose the search engine used in the address bar</p>
            </div>
          </div>
          
          <div className="ml-9">
            <Select 
              value={settings.searchEngine} 
              onValueChange={(val) => updateSettings({ searchEngine: val })}
            >
              <SelectTrigger className="w-64">
                <SelectValue placeholder="Select search engine" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Google">Google</SelectItem>
                <SelectItem value="DuckDuckGo (HTML)">DuckDuckGo (HTML)</SelectItem>
                <SelectItem value="Bing">Bing</SelectItem>
                <SelectItem value="Brave Search">Brave Search</SelectItem>
                <SelectItem value="DuckDuckGo">DuckDuckGo</SelectItem>
                <SelectItem value="Wikipedia">Wikipedia</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </section>

        {/* Appearance */}
        <section className="bg-card border border-border rounded-xl p-6 shadow-sm">
          <div className="flex items-start gap-4 mb-6">
            <Palette className="h-5 w-5 text-muted-foreground mt-0.5" />
            <div>
              <h2 className="text-lg font-semibold">Appearance</h2>
              <p className="text-sm text-muted-foreground">Customize how the browser looks</p>
            </div>
          </div>
          
          <div className="ml-9 space-y-6">
            <div className="space-y-3">
              <Label>Theme</Label>
              <Select 
                value={settings.theme} 
                onValueChange={(val: any) => updateSettings({ theme: val })}
              >
                <SelectTrigger className="w-64">
                  <SelectValue placeholder="Select theme" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="system">System default</SelectItem>
                  <SelectItem value="light">Light</SelectItem>
                  <SelectItem value="dark">Dark</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </section>

        {/* Privacy & Security */}
        <section className="bg-card border border-border rounded-xl p-6 shadow-sm">
          <div className="flex items-start gap-4 mb-6">
            <Shield className="h-5 w-5 text-muted-foreground mt-0.5" />
            <div>
              <h2 className="text-lg font-semibold">Privacy and security</h2>
              <p className="text-sm text-muted-foreground">Manage your browsing data</p>
            </div>
          </div>
          
          <div className="ml-9">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive">
                  <Trash2 className="mr-2 h-4 w-4" />
                  Clear browsing data
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Clear all browsing data?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will permanently delete your history, bookmarks, pinned shortcuts, and reset all settings to their defaults. This action cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction 
                    onClick={clearAllData}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    Clear data
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </section>
      </div>
    </div>
  );
}
