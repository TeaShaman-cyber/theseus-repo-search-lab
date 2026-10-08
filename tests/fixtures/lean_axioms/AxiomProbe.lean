namespace EvidenceClassCanary

theorem fully_proved : True := by trivial
theorem admitted : False := by sorry

#print axioms EvidenceClassCanary.fully_proved
#print axioms EvidenceClassCanary.admitted

end EvidenceClassCanary
