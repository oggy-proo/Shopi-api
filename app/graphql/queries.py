"""
GraphQL operation documents for Shopify Single-Page Checkout (unstable/graphql)
Clean, modern, and high-performance queries without deprecated types.
"""

QUERY_PROPOSAL_SHIPPING = """query Proposal($sessionInput: SessionTokenInput!, $delivery: DeliveryTermsInput, $merchandise: MerchandiseTermInput, $payment: PaymentTermInput, $buyerIdentity: BuyerIdentityTermInput, $taxes: TaxTermInput, $discounts: DiscountTermsInput, $queueToken: String, $checkpointData: String) {
  session(sessionInput: $sessionInput) {
    negotiate(input: {purchaseProposal: {delivery: $delivery, merchandise: $merchandise, payment: $payment, buyerIdentity: $buyerIdentity, taxes: $taxes, discounts: $discounts}, queueToken: $queueToken, checkpointData: $checkpointData}) {
      __typename
      result {
        ... on NegotiationResultAvailable {
          checkpointData
          queueToken
          sellerProposal {
            __typename
            runningTotal {
              ... on MoneyValueConstraint {
                value {
                  amount
                  currencyCode
                  __typename
                }
                __typename
              }
              __typename
            }
            total {
              ... on MoneyValueConstraint {
                value {
                  amount
                  currencyCode
                  __typename
                }
                __typename
              }
              __typename
            }
            checkoutTotal {
              ... on MoneyValueConstraint {
                value {
                  amount
                  currencyCode
                  __typename
                }
                __typename
              }
              __typename
            }
            delivery {
              ... on FilledDeliveryTerms {
                deliveryLines {
                  availableDeliveryStrategies {
                    ... on CompleteDeliveryStrategy {
                      handle
                      amount {
                        ... on MoneyValueConstraint {
                          value {
                            amount
                            currencyCode
                            __typename
                          }
                          __typename
                        }
                        __typename
                      }
                      __typename
                    }
                    __typename
                  }
                  __typename
                }
                __typename
              }
              __typename
            }
            tax {
              ... on FilledTaxTerms {
                totalTaxAmount {
                  ... on MoneyValueConstraint {
                    value {
                      amount
                      currencyCode
                      __typename
                    }
                    __typename
                  }
                  __typename
                }
                __typename
              }
              __typename
            }
            payment {
              ... on FilledPaymentTerms {
                availablePaymentLines {
                  paymentMethod {
                    ... on PaymentProvider {
                      paymentMethodIdentifier
                      name
                      displayName
                      extensibilityDisplayName
                      __typename
                    }
                    ... on OffsiteProvider {
                      paymentMethodIdentifier
                      name
                      __typename
                    }
                    ... on CustomOnsiteProvider {
                      paymentMethodIdentifier
                      name
                      __typename
                    }
                    ... on ManualPaymentMethodConfig {
                      id
                      name
                      paymentMethodIdentifier
                      __typename
                    }
                    ... on CustomPaymentMethodConfig {
                      id
                      name
                      paymentMethodIdentifier
                      __typename
                    }
                    __typename
                  }
                  __typename
                }
                __typename
              }
              __typename
            }
          }
        }
        ... on CheckpointDenied {
          redirectUrl
          __typename
        }
        ... on Throttled {
          pollAfter
          queueToken
          pollUrl
          __typename
        }
        ... on NegotiationResultFailed {
          __typename
        }
      }
      errors {
        code
        localizedMessage
        nonLocalizedMessage
        localizedMessageHtml
      }
    }
  }
}"""

QUERY_PROPOSAL_DELIVERY = QUERY_PROPOSAL_SHIPPING

MUTATION_SUBMIT = """mutation SubmitForCompletion($input: NegotiationInput!, $attemptToken: String!, $metafields: [MetafieldInput!], $postPurchaseInquiryResult: PostPurchaseInquiryResultCode, $analytics: AnalyticsInput) {
  submitForCompletion(input: $input, attemptToken: $attemptToken, metafields: $metafields, postPurchaseInquiryResult: $postPurchaseInquiryResult, analytics: $analytics) {
    __typename
    ... on SubmitSuccess {
      receipt {
        ... ReceiptDetails
        __typename
      }
      __typename
    }
    ... on SubmitAlreadyAccepted {
      receipt {
        ... ReceiptDetails
        __typename
      }
      __typename
    }
    ... on SubmitFailed {
      reason
      __typename
    }
    ... on SubmitRejected {
      errors {
        code
        localizedMessage
        nonLocalizedMessage
        localizedMessageHtml
        ... on NegotiationError {
          code
          localizedMessage
          nonLocalizedMessage
          __typename
        }
        ... on InputValidationError {
          field
          __typename
        }
        __typename
      }
      __typename
    }
    ... on Throttled {
      pollAfter
      pollUrl
      queueToken
      __typename
    }
    ... on CheckpointDenied {
      redirectUrl
      __typename
    }
    ... on SubmittedForCompletion {
      receipt {
        ... ReceiptDetails
        __typename
      }
      __typename
    }
  }
}

fragment ReceiptDetails on Receipt {
  __typename
  ... on ProcessedReceipt {
    id
    token
    redirectUrl
    orderStatusPageUrl
    orderIdentity {
      id
      buyerIdentifier
      __typename
    }
    __typename
  }
  ... on ProcessingReceipt {
    id
    pollDelay
    __typename
  }
  ... on WaitingReceipt {
    id
    pollDelay
    __typename
  }
  ... on ActionRequiredReceipt {
    id
    action {
      ... on CompletePaymentChallenge {
        offsiteRedirect
        url
        __typename
      }
      ... on CompletePaymentChallengeV2 {
        challengeType
        challengeData
        __typename
      }
      __typename
    }
    timeout {
      millisecondsRemaining
      __typename
    }
    __typename
  }
  ... on FailedReceipt {
    id
    processingError {
      ... on PaymentFailed {
        code
        messageUntranslated
        hasOffsitePaymentMethod
        __typename
      }
      ... on OrderCreationFailure {
        paymentsHaveBeenReverted
        __typename
      }
      ... on InventoryClaimFailure {
        __typename
      }
      __typename
    }
    __typename
  }
}"""

QUERY_POLL = """query PollForReceipt($receiptId: ID!, $sessionToken: String!) {
  receipt(receiptId: $receiptId, sessionInput: {sessionToken: $sessionToken}) {
    ... ReceiptDetails
    __typename
  }
}

fragment ReceiptDetails on Receipt {
  __typename
  ... on ProcessedReceipt {
    id
    token
    redirectUrl
    orderStatusPageUrl
    orderIdentity {
      id
      buyerIdentifier
      __typename
    }
    __typename
  }
  ... on ProcessingReceipt {
    id
    pollDelay
    __typename
  }
  ... on WaitingReceipt {
    id
    pollDelay
    __typename
  }
  ... on ActionRequiredReceipt {
    id
    action {
      ... on CompletePaymentChallenge {
        offsiteRedirect
        url
        __typename
      }
      ... on CompletePaymentChallengeV2 {
        challengeType
        challengeData
        __typename
      }
      __typename
    }
    timeout {
      millisecondsRemaining
      __typename
    }
    __typename
  }
  ... on FailedReceipt {
    id
    processingError {
      ... on PaymentFailed {
        code
        messageUntranslated
        hasOffsitePaymentMethod
        __typename
      }
      ... on OrderCreationFailure {
        paymentsHaveBeenReverted
        __typename
      }
      ... on InventoryClaimFailure {
        __typename
      }
      __typename
    }
    __typename
  }
}"""
