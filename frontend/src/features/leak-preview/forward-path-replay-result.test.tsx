// @vitest-environment jsdom
import { act,cleanup,fireEvent,render,screen } from '@testing-library/react'
import { afterEach,expect,it } from 'vitest'
import { ForwardPathReplayResult } from './forward-path-replay-result'
globalThis.ResizeObserver=class {observe(){} unobserve(){} disconnect(){}}
afterEach(cleanup)
it('distinguishes stochastic divergence and shows the first segment without claiming a production hit',async()=>{
  render(<ForwardPathReplayResult report={{state:'not_reached',reason:'different_sampled_branch',included_in_flux:false,execution_backend:'cpu',
    matched_surface_count:1,path_reproduced:false,first_difference:{segment_index:1,expected_point:[1,2,3],actual_point:null}}}/> )
  expect(screen.getByText(/확률적 반사 분기 변경/)).toBeTruthy()
  expect(screen.getByText(/CPU · 진단용 1 Ray · 본 광량 미포함/)).toBeTruthy()
  expect(screen.getByText(/첫 불일치 구간 2/)).toBeTruthy()
  fireEvent.click(screen.getByText(/대표 경로 순방향 재생/))
  act(()=>screen.getByRole('button',{name:'순방향 경로 재생 도움말'}).focus())
  expect((await screen.findAllByText(/확률적 반사는 같은 방향을 강제하지/)).length).toBeGreaterThan(0)
})
it('shows reproduced arrival only when the production replay follows the representative path',()=>{
  render(<ForwardPathReplayResult report={{state:'receiver_reached',reason:'receiver_reached',included_in_flux:false,execution_backend:'cpu',path_reproduced:true}}/> )
  expect(screen.getByText(/동일 경로 도달/)).toBeTruthy()
})
