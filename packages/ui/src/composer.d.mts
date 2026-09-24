export const IMAGE_TYPES: string[]
export const MAX_IMAGE_BYTES: number
export function clipboardImages(data: DataTransfer | null): File[]
export function imageError(file: File): string
export class ScrollFollow {
  following: boolean
  lastTop: number
  constructor(element: () => HTMLElement | null, changed?: (following: boolean) => void)
  set(value: boolean): void
  reset(): void
  pause(): void
  resume(): void
  scrolled(): void
  sync(): void
}
