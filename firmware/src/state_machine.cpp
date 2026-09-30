#include "firmware/state_machine.hpp"

namespace firmware {

Reduction reduce(const State current, const Event event) noexcept {
  if (event == Event::failure) {
    return {State::degraded, Error::ok};
  }
  if (current == State::degraded && event == Event::retry) {
    return {State::connect, Error::ok};
  }

  struct Transition final {
    State from;
    Event event;
    State to;
  };
  static constexpr Transition transitions[] = {
      {State::boot, Event::booted, State::recover},
      {State::recover, Event::recovered, State::connect},
      {State::connect, Event::connected, State::poll},
      {State::poll, Event::manifest_available, State::download},
      {State::download, Event::downloaded, State::verify},
      {State::verify, Event::verified, State::promote},
      {State::promote, Event::promoted, State::display},
      {State::display, Event::displayed, State::commit},
      {State::commit, Event::committed, State::poll},
  };
  for (const auto& transition : transitions) {
    if (transition.from == current && transition.event == event) {
      return {transition.to, Error::ok};
    }
  }
  return {current, Error::invalid_transition};
}

}  // namespace firmware
