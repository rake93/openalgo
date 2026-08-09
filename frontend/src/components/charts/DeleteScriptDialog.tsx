/**
 * Destructive confirm for deleting a saved OpenScript indicator.
 *
 * Separate from `ScriptMenu` so the menu stays presentational and this can be
 * tested without opening a dropdown - the same split every other editor dialog
 * makes (`VersionHistoryDialog`, `CreateAlertDialog`).
 *
 * The dialog spells out BOTH consequences rather than a generic "cannot be
 * undone", because the second one is invisible from the editor: the server
 * cascades the delete to every stored version, and any alert built on one of
 * those versions is left pointing at a row that no longer exists. SQLite does
 * not enforce that foreign key unless `PRAGMA foreign_keys=ON` is armed on the
 * connection, and `NullPool` hands out a fresh connection per operation, so the
 * delete succeeds and the alerts are simply orphaned. A reader who is not told
 * that will find out when an alert stops firing.
 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

interface DeleteScriptDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Shown so the user confirms against a name, not against "this script". */
  scriptName: string
  /**
   * Alerts built on a version of this script, or null while unknown.
   *
   * Null is a real state, not zero: the count is a best-effort lookup and a
   * failed one must not claim "0 alerts" - that is the reassuring answer, and
   * it would be the one shown exactly when the server is unreachable.
   */
  affectedAlerts: number | null
  busy: boolean
  onConfirm: () => void
}

export function DeleteScriptDialog({
  open,
  onOpenChange,
  scriptName,
  affectedAlerts,
  busy,
  onConfirm,
}: DeleteScriptDialogProps) {
  const name = scriptName.trim() || 'Untitled indicator'

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this indicator?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">
              <p>
                <span className="font-medium">{name}</span> and every saved version of it will be
                deleted. This cannot be undone.
              </p>
              {affectedAlerts !== null && affectedAlerts > 0 && (
                <p className="text-destructive">
                  {affectedAlerts === 1
                    ? '1 alert built on this indicator will stop working.'
                    : `${affectedAlerts} alerts built on this indicator will stop working.`}{' '}
                  Deleting the indicator does not remove them.
                </p>
              )}
              {affectedAlerts === null && (
                <p>Any alerts built on this indicator will stop working.</p>
              )}
              <p className="text-muted-foreground">
                Charts using it keep their own copy of the compiled indicator until they are
                reloaded.
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Keep</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              // The dialog closes on its own action click; deletion is async and
              // its failure has to stay visible, so the parent owns the close.
              e.preventDefault()
              onConfirm()
            }}
            disabled={busy}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {busy ? 'Deleting…' : 'Delete'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
