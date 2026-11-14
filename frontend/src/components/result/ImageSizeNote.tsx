import { InfoIcon } from './InfoIcon'

/**
 * The unexpected_image_size warning as a quiet note under the result. It comes with
 * most scans that are not 512×512 and matters mainly when a glioma is suspected,
 * so it is not shown as a yellow block.
 */
export function ImageSizeNote({ message }: { message: string }) {
  return (
    <p role="note" className="mt-2 flex gap-1.5 text-xs text-slate-500 dark:text-slate-400">
      <InfoIcon />
      <span>{message}</span>
    </p>
  )
}
