import type { ClassName } from '../api/types'

/** Order of the model outputs, as returned by the backend. */
export const CLASS_ORDER: readonly ClassName[] = ['glioma', 'meningioma', 'notumor', 'pituitary']

export const CLASS_LABELS: Record<ClassName, string> = {
  glioma: 'Гліома',
  meningioma: 'Менінгіома',
  notumor: 'Пухлини не виявлено',
  pituitary: 'Пухлина гіпофіза',
}
