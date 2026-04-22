import { Download } from "lucide-react";

export function DownloadsPage() {
  return (
    <div className="max-w-4xl mx-auto py-8 px-6 min-h-[80vh] flex flex-col items-center justify-center">
      <div className="h-24 w-24 rounded-full bg-secondary/50 flex items-center justify-center mb-6">
        <Download className="h-10 w-10 text-muted-foreground" />
      </div>
      <h1 className="text-2xl font-bold mb-3">Downloads</h1>
      <p className="text-muted-foreground text-center max-w-md">
        File downloads are not supported in this sandboxed browser environment for security reasons.
      </p>
    </div>
  );
}
