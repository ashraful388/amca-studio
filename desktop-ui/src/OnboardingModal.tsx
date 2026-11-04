import React, { useState } from "react";
import { getOnboardingSteps, OnboardingStep } from "./onboardingAndGuidanceModel";

export interface OnboardingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete?: () => void;
}

export const OnboardingModal: React.FC<OnboardingModalProps> = ({
  isOpen,
  onClose,
  onComplete,
}) => {
  const steps = getOnboardingSteps();
  const [currentStepIdx, setCurrentStepIdx] = useState<number>(0);

  if (!isOpen) return null;

  const currentStep: OnboardingStep = steps[currentStepIdx] || steps[0];
  const isFirst = currentStepIdx === 0;
  const isLast = currentStepIdx === steps.length - 1;

  const handleNext = () => {
    if (isLast) {
      if (onComplete) onComplete();
      onClose();
    } else {
      setCurrentStepIdx((prev) => Math.min(prev + 1, steps.length - 1));
    }
  };

  const handleBack = () => {
    setCurrentStepIdx((prev) => Math.max(prev - 1, 0));
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
      <div className="modal-container onboarding-modal">
        <div className="modal-header">
          <div>
            <span className="section-kicker">First-Launch Tour</span>
            <h2 id="onboarding-title">{currentStep.title}</h2>
          </div>
          <button type="button" className="close-button" onClick={onClose} aria-label="Close tour">
            &times;
          </button>
        </div>

        <div className="modal-body">
          <div className="step-indicator-bar">
            {steps.map((s, idx) => (
              <div
                key={s.stepId}
                className={`step-dot ${idx === currentStepIdx ? "active" : ""} ${
                  idx < currentStepIdx ? "completed" : ""
                }`}
                title={`Step ${idx + 1}: ${s.title}`}
              />
            ))}
          </div>

          <p className="step-summary">{currentStep.summary}</p>

          <ul className="step-details-list">
            {currentStep.details.map((detail, i) => (
              <li key={i}>{detail}</li>
            ))}
          </ul>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleBack}
            disabled={isFirst}
          >
            Back
          </button>
          <div className="step-counter">
            Step {currentStepIdx + 1} of {steps.length}
          </div>
          <button type="button" className="btn btn-primary" onClick={handleNext}>
            {isLast ? "Finish & Start" : currentStep.userActionLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

