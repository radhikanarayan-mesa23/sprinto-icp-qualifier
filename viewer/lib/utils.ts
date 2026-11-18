import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// shadcn's standard helper. The pasted component imported `cn` from an npm package named "cn"; this is the usual home.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
