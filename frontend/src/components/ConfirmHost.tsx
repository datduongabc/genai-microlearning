import { useEffect, useRef, useState } from "react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "./ui/alert-dialog";
import { setConfirmHost, type ConfirmRequest } from "../lib/confirm";
import { focusReturnTarget } from "../lib/focusReturn";

export function ConfirmHost() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  useEffect(() => {
    setConfirmHost((next) => { returnTo.current = focusReturnTarget(); setRequest(next); });
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
