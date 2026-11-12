interface HeatmapViewProps {
  scanUrl: string
  heatmapBase64?: string | null
}

const frame = 'flex aspect-square items-center justify-center overflow-hidden rounded-lg bg-black'

/** The uploaded scan and the Grad-CAM heat map side by side. */
export function HeatmapView({ scanUrl, heatmapBase64 }: HeatmapViewProps) {
  return (
    <div className="space-y-3">
      <div className="grid gap-4 sm:grid-cols-2">
        <figure className="space-y-2">
          <div className={frame}>
            <img src={scanUrl} alt="Завантажений знімок" className="size-full object-contain" />
          </div>
          <figcaption className="text-center text-sm text-slate-600 dark:text-slate-300">
            Знімок
          </figcaption>
        </figure>

        <figure className="space-y-2">
          {heatmapBase64 ? (
            <div className={frame}>
              <img
                src={`data:image/png;base64,${heatmapBase64}`}
                alt="Теплова карта Grad-CAM"
                className="size-full object-contain"
              />
            </div>
          ) : (
            <div className="flex aspect-square items-center justify-center rounded-lg border-2 border-dashed border-slate-300 p-4 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
              Теплову карту не запитували. Увімкніть «Показати теплову карту» і проаналізуйте знімок
              ще раз.
            </div>
          )}
          <figcaption className="text-center text-sm text-slate-600 dark:text-slate-300">
            Теплова карта Grad-CAM
          </figcaption>
        </figure>
      </div>

      {heatmapBase64 && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Червоні ділянки найбільше вплинули на рішення моделі, сині — найменше. Карта показує, на
          що зважала модель, а не межі пухлини.
        </p>
      )}
    </div>
  )
}
