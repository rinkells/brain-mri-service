// Short names for the types generated from the backend's OpenAPI schema.
// Regenerate schema.d.ts with `npm run generate:api` after changing the API.
import type { components } from './schema'

type Schemas = components['schemas']

export type HealthResponse = Schemas['HealthResponse']
export type PredictionResponse = Schemas['PredictionResponse']
export type PredictionWarning = Schemas['PredictionWarning']
export type ErrorResponse = Schemas['ErrorResponse']
export type ValidationErrorResponse = Schemas['HTTPValidationError']
export type ClassName = PredictionResponse['predicted_class']

export type PredictionRecord = Schemas['PredictionRecordResponse']
export type PredictionPage = Schemas['PredictionPage']
export type StatsResponse = Schemas['StatsResponse']
