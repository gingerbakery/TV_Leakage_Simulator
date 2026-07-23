import { useState, type PropsWithChildren } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'

import { TooltipProvider } from '@/components/ui/tooltip'

import { createAppQueryClient } from './query-client'

export function AppProviders({ children }: PropsWithChildren) {
  const [queryClient] = useState(createAppQueryClient)

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}
