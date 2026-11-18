import { useEffect, useRef, useState } from "react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "./ui/alert-dialog";
import { setConfirmHost, type ConfirmRequest } from "../lib/confirm";

// Where focus goes when the question closes. A menu item that asked is gone by then, so its
// menu's button; otherwise whatever had focus.
function returnTarget(): HTMLElement | null {
  const active = document.activeElement as HTMLElement | null;
  const menu = active?.closest("[role=menu]");
  return (menu && document.getElementById(menu.getAttribute("aria-labelledby") ?? "")) || active;
}

export function ConfirmHost() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  useEffect(() => {
    setConfirmHost((next) => { returnTo.current = returnTarget(); setRequest(next); });
    return () => setConfirmHost(null);
  }, []);
  // Every way of closing that is not the action button (Esc, Cancel) answers no.
  const answer = (ok: boolean) => { request?.resolve(ok); setRequest(null); };
  return (
    <AlertDialog open={request !== null} onOpenChange={(open) => { if (!open) answer(false); }}>
      {request && <AlertDialogContent className="confirm-dialog" onCloseAutoFocus={(event) => { event.preventDefault(); returnTo.current?.focus(); }}>
        <AlertDialogHeader>
          <AlertDialogTitle>{request.title}</AlertDialogTitle>
          {request.description && <AlertDialogDescription>{request.description}</AlertDialogDescription>}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Hủy</AlertDialogCancel>
          <AlertDialogAction variant={request.destructive ? "destructive" : "default"} onClick={() => answer(true)}>
            {request.action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>}
    </AlertDialog>
  );
}
